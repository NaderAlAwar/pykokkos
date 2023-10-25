import inspect
from dataclasses import dataclass
from typing import  Callable, Dict, Optional, Tuple, Union, List, Any
import pykokkos.kokkos_manager as km
from .execution_policy import MDRangePolicy, TeamPolicy, TeamThreadRange, RangePolicy, ExecutionPolicy, ExecutionSpace
from .views import View, ViewType
from .layout import Layout, get_default_layout
from .data_types import DataType, DataTypeClass

@dataclass
class HandledArgs:
    """
    Class for holding the arguments passed to parallel_* functions
    """

    name: Optional[str]
    policy: ExecutionPolicy
    workunit: Callable
    view: Optional[ViewType]
    initial_value: Union[int, float]


@dataclass
class UpdatedTypes:
    """
    Class for storing inferred type annotation information 
    (Making Pykokkos more pythonic by automatically inferring types)
    """

    workunit: Callable
    inferred_types: Dict[str, str] # type information stored as string: identifier -> type
    param_list: List[str]
    layout_change: Dict[str, str] # layout for views
    types_signature: str # unique string identifer for inferred paramater types


# DataType class has all supported pk datatypes, we ignore class members starting with __, add enum duplicate aliases
SUPPORTED_NP_DTYPES = [attr for attr in dir(DataType) if not attr.startswith("__")] + ["float64", "float32"]


def handle_args(is_for: bool, *args) -> HandledArgs:
    """
    Handle the *args passed to parallel_* functions

    :param is_for: whether the arguments belong to a parallel_for call
    :param *args: the list of arguments being checked
    :returns: a HandledArgs object containing the passed arguments
    """

    unpacked: Tuple = tuple(*args)

    name: Optional[str] = None
    policy: Union[ExecutionPolicy, int]
    workunit: Callable
    view: Optional[ViewType] = None
    initial_value: Union[int, float] = 0


    if len(unpacked) == 2:
        policy = unpacked[0]
        workunit = unpacked[1]

    elif len(unpacked) == 3:
        if isinstance(unpacked[0], str):
            name = unpacked[0]
            policy = unpacked[1]
            workunit = unpacked[2]
        elif is_for and isinstance(unpacked[2], ViewType):
            policy = unpacked[0]
            workunit = unpacked[1]
            view = unpacked[2]
        elif isinstance(unpacked[2], (int, float)):
            policy = unpacked[0]
            workunit = unpacked[1]
            initial_value = unpacked[2]
        else:
            raise TypeError(f"ERROR: wrong arguments {unpacked}")

    elif len(unpacked) == 4:
        if isinstance(unpacked[0], str):
            name = unpacked[0]
            policy = unpacked[1]
            workunit = unpacked[2]

            if is_for and isinstance(unpacked[3], ViewType):
                view = unpacked[3]
            elif isinstance(unpacked[3], (int, float)):
                initial_value = unpacked[3]
            else:
                raise TypeError(f"ERROR: wrong arguments {unpacked}")
        else:
            raise TypeError(f"ERROR: wrong arguments {unpacked}")

    else:
        raise ValueError(f"ERROR: incorrect number of arguments {len(unpacked)}")

    if isinstance(policy, int):
        policy = RangePolicy(km.get_default_space(), 0, policy)

    return HandledArgs(name, policy, workunit, view, initial_value)


def get_annotations(parallel_type: str, handled_args: HandledArgs, *args, passed_kwargs) -> Optional[UpdatedTypes]:
    '''
    Infer the datatypes for arguments passed against workunit parameters

    :param parallel_type: A string identifying the type of parallel dispatch ("parallel_for", "parallel_reduce" ...)
    :param handled_args: Processed arguments passed to the dispatch
    :param args: raw arguments passed to the dispatch
    :param passed_kwargs: raw keyword arguments passed to the dispatch
    :returns: UpdateTypes object or None if there are no annotations to be inferred
    '''

    param_list = list(inspect.signature(handled_args.workunit).parameters.values())
    args_list = list(*args)
    updated_types = UpdatedTypes(workunit=handled_args.workunit, inferred_types={}, param_list=param_list, layout_change={}, types_signature=None)
    policy_params: int = len(handled_args.policy.begin) if isinstance(handled_args.policy, MDRangePolicy) else 1

    # accumulator 
    if parallel_type == "parallel_reduce":
        policy_params += 1
    # accumulator + lass_pass
    if parallel_type == "parallel_scan":
        policy_params += 2

    # Handling policy parameters
    updated_types = infer_policy_args(param_list, policy_params, handled_args.policy, parallel_type, updated_types)

    # Policy parameters are the only parameters
    if len(param_list) == policy_params:
        if not len(updated_types.inferred_types): return None
        return updated_types

    # Handle keyword args, make sure they are treated by queuing them in args
    if len(passed_kwargs):
        # add value to arguments list so the value can be assessed
        for param in param_list[policy_params:]:
            if param.name in passed_kwargs:
                args_list.append(passed_kwargs[param.name])

    # Handling arguments other than policy args, they begin at value_idx in args list
    # e.g idx=3 -> parallel_for("label", policy, workunit, <other args>...) or if name ("label") is missing: 2 
    value_idx: int = 3 if handled_args.name != None else 2 

    assert (len(param_list) - policy_params) == len(args_list) - value_idx, f"Unannotated arguments mismatch {len(param_list) - policy_params} != {len(args_list) - value_idx}"

    # At this point there must more arguments to the workunit that may not have their types annotated
    # These parameters may also not have raw values associated in the stand alone format -> infer types from the argument list

    updated_types = infer_other_args(param_list, policy_params, args_list, value_idx, handled_args.policy.space, updated_types)

    if not len(updated_types.inferred_types) and not len(updated_types.layout_change): return None

    updated_types.types_signature = get_types_sig(updated_types.inferred_types, updated_types.layout_change)

    return updated_types

def infer_policy_args(
    param_list: List[inspect.Parameter],
    policy_params: int,
    policy: ExecutionPolicy,
    parallel_type: str,
    updated_types: UpdatedTypes
    ) -> UpdatedTypes:
    '''
    Infer the types of policy arguments

    :param param_list: list of parameter objects that are present in the workunit signature
    :param policy_params: the number of initial parameters that are dedicated to policy (in param_list/signature)
    :param policy: the pykokkos execution policy for workunit
    :param parallel_type: "parallel_for" or "parallel_reduce" or "parallel_scan"
    :param updated_types: UpdatedTypes object to store inferred types information
    :returns: Updated UpdatedTypes object with inferred types
    '''

    for i in range(policy_params):
        param = param_list[i]

        if param.annotation is not inspect._empty:
            continue

        # Check policy and apply annotation(s)
        if isinstance(policy, RangePolicy) or isinstance(policy, TeamThreadRange):
            # only expects one param
            if i == 0:
                updated_types.inferred_types[param.name] = "int"

        elif isinstance(policy, TeamPolicy):
            if i == 0:
                updated_types.inferred_types[param.name] = 'TeamMember'

        elif isinstance(policy, MDRangePolicy):
            total_dims = len(policy.begin) 
            if i < total_dims:
                updated_types.inferred_types[param.name] = "int"
        else:
            raise ValueError("Automatic annotations not supported for this policy")

        # last policy param for parallel reduce and second last for parallel_scan is always the accumulator; the default type is double
        if i == policy_params - 1 and parallel_type == "parallel_reduce" or i == policy_params - 2 and parallel_type == "parallel_scan":
            updated_types.inferred_types[param.name] = "Acc:double"

        if i == policy_params - 1 and parallel_type == "parallel_scan":
            updated_types.inferred_types[param.name] = "bool"

    return updated_types


def infer_other_args(
    param_list: List[inspect.Parameter], 
    policy_params: int,
    args_list: List[Any],
    start_idx: int,
    space: ExecutionSpace,
    updated_types: UpdatedTypes
    ) -> UpdatedTypes:
    '''
    Infer the types of arguments (after the policy arguments)

    :param param_list: list of parameter objects that are present in the workunit signature
    :param policy_params: the number of initial parameters that are dedicated to policy (in param_list/signature)
    :param args_list: List of arguments passed to the parallel dispactch (e.g args for parallal_for())
    :param start_idx: The index for the first non policy argument in args_list
    :param updated_types: UpdatedTypes object to store inferred types information
    :returns: Updated UpdatedTypes object with inferred types
    '''

    for i in range(policy_params , len(param_list)):
        param = param_list[i]
        value = args_list[start_idx + i - policy_params]

        if isinstance(value, View):
            inferred_layout = value.layout if value.layout is not Layout.LayoutDefault else get_default_layout(space)
            updated_types.layout_change[param.name] = "LayoutRight" if inferred_layout == Layout.LayoutRight else "LayoutLeft"

        if param.annotation is not inspect._empty:
            continue

        param_type = type(value).__name__

        # switch integer values over 31 bits (signed positive value) to numpy:int64
        if param_type == "int" and value.bit_length() > 31:
            param_type = "numpy:int64"

        # check if package name is numpy (handling numpy primitives)
        pckg_name = type(value).__module__

        if pckg_name == "numpy":
            if param_type not in SUPPORTED_NP_DTYPES:
                err_str = f"Numpy type {param_type} is unsupported"
                raise TypeError(err_str)

            if param_type == "float64": param_type = "double"
            if param_type == "float32": param_type = "float"
            # numpy:<type>, Will switch to pk.<type> in parser.fix_types
            param_type = pckg_name +":"+ param_type

        if isinstance(value, View):
            view_dtype = get_pk_datatype(value.dtype)
            if not view_dtype:
                raise TypeError("Cannot infer datatype for view:", param.name)

            param_type = "View"+str(len(value.shape))+"D:"+view_dtype

        updated_types.inferred_types[param.name] = param_type 

    return updated_types


def get_pk_datatype(view_dtype):
    '''
    :param view_dtype: view.dtype whose datatype is to be determined as string
    :returns: the type of custom pkDataType as string
    '''

    dtype = None
    if isinstance(view_dtype, DataType):
        dtype = str(view_dtype.name)

    elif inspect.isclass(view_dtype) and issubclass(view_dtype, DataTypeClass):
        dtype = str(view_dtype.__name__)

    if dtype == "float64": dtype = "double"
    if dtype == "float32": dtype = "float"

    return dtype


def get_types_sig(inferred_types: Dict[str, str], inferred_layouts: Dict[str, str]) -> str:
    '''
    :param inferred_types: Dict that stores arg name against its inferred type
    :param inferred_layouts: Dict that stores view name against its inferred layout
    :returns: a string representing inferred types
    '''

    if not len(inferred_layouts) and not len(inferred_types):
        return None

    signature:str = ""
    for name, i_type in inferred_types.items():
        signature += i_type
        if "View" in i_type and name in inferred_layouts:
            signature += inferred_layouts[name]

    # if there were no inferred types but only layouts
    if signature == "":
        for name, l_type in inferred_layouts.items():
            signature += name + l_type

    # Compacting
    signature = signature.replace("View", "")
    signature = signature.replace("Acc:", "" )
    signature = signature.replace("TeamMember", "T")
    signature = signature.replace("numpy:", "np")
    signature = signature.replace("LayoutRight", "R")
    signature = signature.replace("LayoutLeft", "L")
    signature = signature.replace(":", "")
    signature = signature.replace("double", "d")
    signature = signature.replace("int", "i")
    signature = signature.replace("bool", "b")
    signature = signature.replace("float", "f")

    return signature

def get_type_str(inspect_type: inspect.Parameter.annotation) -> str:
    '''
    Given a user provided inspect.annotation string return the equivalent type inferrence string (used internally).
    This function is typically invoked when resetting the AST

    :param inspect_type: annotation object provided by inspect package
    :return: string for the same type as supported in type_inference.py
    '''

    basic_type = None
    if isinstance(inspect_type, type):
        basic_type = str(inspect_type.__name__)
    else:
        # Support for python 3.8, string manip needed :(
        t_str = str(inspect_type)
        t_str = t_str.replace("pykokkos.interface.data_types.", "")
        t_str = t_str.replace("pykokkos.interface.views.", "")
        if ".Acc[" in t_str:
            basic_type = "Acc"
        elif "TeamMember" in t_str:
            basic_type = "TeamMember"
        elif "View" in t_str:
            basic_type = (t_str.split('[')[0]).strip()

    assert basic_type is not None, f"Inference failed for {inspect_type}"

    # just a basic primitive
    if "pykokkos" not in str(inspect_type):
        return basic_type

    if basic_type == "Acc":
        return "Acc:double"

    if basic_type == "TeamMember":
        return "TeamMember"

    type_str = str(inspect_type).replace('pykokkos.interface.data_types.', 'pk.')

    if "views" in type_str:
        # is a view, only need the slice
        type_str = type_str.split('[')[1]
        type_str = type_str[:-1]
        type_str = type_str.replace("pk.", "")

        return basic_type+":"+type_str
    
    # just a numpy primitive
    if "pk." in type_str and basic_type in SUPPORTED_NP_DTYPES:
        type_str = "numpy:" + basic_type
        return type_str

    err_str = f"User provided unsupported annotation: {inspect_type}"
    raise TypeError(err_str)
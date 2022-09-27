import os
from types import ModuleType
from typing import Any, Dict, List

from pykokkos.bindings import kokkos
from pykokkos.interface.execution_space import ExecutionSpace
from pykokkos.interface.data_types import DataTypeClass, double

CONSTANTS: Dict[str, Any] = {
    "EXECUTION_SPACE": ExecutionSpace.OpenMP,
    "REAL_DTYPE": double,
    "IS_INITIALIZED": False,
    "ENABLE_UVM": False,
    "MULTI_GPU": False,
    "NUM_GPUS": 0,
    "KOKKOS_GPU_MODULE": kokkos,
    "KOKKOS_GPU_MODULE_LIST": [],
    "DEVICE_ID": 0
}

def get_default_space() -> ExecutionSpace:
    """
    Get the default PyKokkos execution space

    :returns: the ExecutionSpace object
    """

    if os.environ.get("DEBUG"):
        return ExecutionSpace.Debug

    return CONSTANTS["EXECUTION_SPACE"]

def set_default_space(space: ExecutionSpace) -> None:
    """
    Set the default PyKokkos execution space

    :param space: the new default
    """

    if not isinstance(space, ExecutionSpace):
        print("ERROR: space is not an ExecutionSpace")
        return

    CONSTANTS["EXECUTION_SPACE"] = space

def get_default_precision() -> ExecutionSpace:
    """
    Get the default PyKokkos precision

    :returns: the precision type object
    """

    return CONSTANTS["REAL_DTYPE"]

def set_default_precision(precision: DataTypeClass) -> None:
    """
    Set the default PyKokkos precision

    :param precision: the new default
    """

    if not issubclass(precision, DataTypeClass):
        print("ERROR: precision is not a DataType")
        return

    CONSTANTS["REAL_DTYPE"] = precision

def is_uvm_enabled() -> bool:
    """
    Check if UVM is enabled

    :returns: True or False
    """

    return CONSTANTS["ENABLE_UVM"]

def enable_uvm() -> None:
    """
    Enable CudaUVMSpace
    """

    CONSTANTS["ENABLE_UVM"] = True

def disable_uvm() -> None:
    """
    Disable CudaUVMSpace
    """

    CONSTANTS["ENABLE_UVM"] = False

def initialize() -> None:
    """
    Call Kokkos::initialize() if not already called
    """

    if CONSTANTS["IS_INITIALIZED"] == False:
        kokkos.initialize()
        CONSTANTS["IS_INITIALIZED"] = True

def finalize() -> None:
    """
    Call Kokkos::finalize() if initialize() has been called
    """

    if CONSTANTS["IS_INITIALIZED"] == True:
        kokkos.finalize()
        CONSTANTS["IS_INITIALIZED"] = False

def get_kokkos_module(is_cpu: bool) -> ModuleType:
    """
    Get the current kokkos module

    :param is_cpu: is the lib needed for cpu
    :returns: the kokkos module
    """

    if is_cpu:
        return kokkos

    return CONSTANTS["KOKKOS_GPU_MODULE"]

def set_device_id(device_id: int) -> None:
    """
    Set the current device ID

    :param device_id: the ID of the device to enable
    """

    if not isinstance(device_id, int):
        raise TypeError("'device_id' must be of type 'int'")

    num_gpus: int = CONSTANTS["NUM_GPUS"]
    if device_id >= num_gpus or device_id < 0:
        raise RuntimeError(f"Device {device_id} does not exist (range [0..{num_gpus})")

    if num_gpus == 1:
        return

    import cupy
    cupy.cuda.runtime.setDevice(device_id)
    CONSTANTS["DEVICE_ID"] = device_id

    gpu_lib = CONSTANTS["KOKKOS_GPU_MODULE_LIST"][device_id]
    CONSTANTS["KOKKOS_GPU_MODULE"] = gpu_lib

def get_device_id() -> int:
    """
    Get the ID of the currently enabled device

    :returns: the ID of the enabled device
    """

    return CONSTANTS["DEVICE_ID"]

def is_multi_gpu_enabled() -> bool:
    """
    Check if pykokkos has been configured for multi-gpu use

    :returns: True or False
    """

    return CONSTANTS["MULTI_GPU"]

def get_kokkos_gpu_modules() -> List:
    """
    Get the pykokkos-base gpu modules

    :returns: the list of modules
    """

    return CONSTANTS["KOKKOS_GPU_MODULE_LIST"]

def get_num_gpus() -> bool:
    """
    Get the number of gpus pykokkos has been configured for

    :returns: the number of gpus
    """

    return CONSTANTS["NUM_GPUS"]

try:
    # Import multiple kokkos libs to support multiple devices per
    # process. This assumes that there are modules named f"gpu{id}"
    # that can be imported.
    import atexit
    import cupy as cp
    import importlib
    import sys

    NUM_CUDA_GPUS: int = cp.cuda.runtime.getDeviceCount()
    KOKKOS_LIBS: List[str] = [f"gpu{id}" for id in range(NUM_CUDA_GPUS)]

    KOKKOS_LIB_INSTANCES: List = []
    for id, lib in enumerate(KOKKOS_LIBS):
        module = importlib.import_module(lib)
        KOKKOS_LIB_INSTANCES.append(module)

        # Can't pass device id directly to initialize(), so need to
        # append argument to select device to sys.argv.
        # (see https://github.com/kokkos/pykokkos-base/blob/d3946ed56483f3cbe2e660cc50fe73c50dad19ea/src/libpykokkos.cpp#L65)
        sys.argv.append(f"--device-id={id}")
        module.initialize()
        atexit.register(module.finalize)
        sys.argv.pop()

    CONSTANTS["MULTI_GPU"] = True
    CONSTANTS["NUM_GPUS"] = NUM_CUDA_GPUS
    CONSTANTS["KOKKOS_GPU_MODULE_LIST"] = KOKKOS_LIB_INSTANCES
    CONSTANTS["KOKKOS_GPU_MODULE"] = KOKKOS_LIB_INSTANCES[0]

except Exception:
    pass

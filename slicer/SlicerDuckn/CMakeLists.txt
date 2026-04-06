cmake_minimum_required(VERSION 3.16.3...3.19.7 FATAL_ERROR)

project(SlicerDuckn)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_HOMEPAGE "https://github.com/mhalle/duckn")
set(EXTENSION_CATEGORY "IO")
set(EXTENSION_CONTRIBUTORS "duckn contributors")
set(EXTENSION_DESCRIPTION "Read and write duckn Zarr volumes and ZMP manifests in 3D Slicer.")
set(EXTENSION_ICONURL "https://raw.githubusercontent.com/mhalle/duckn/main/docs/icon.png")
set(EXTENSION_SCREENSHOTURLS "")
set(EXTENSION_DEPENDS "NA")

#-----------------------------------------------------------------------------
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
add_subdirectory(SlicerDucknIO)

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})

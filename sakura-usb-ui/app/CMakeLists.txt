cmake_minimum_required(VERSION 3.20)
project(sakura-usb-ui VERSION 0.1.0)

find_package(ECM 6.0 REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})
include(KDEInstallDirs)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)

find_package(Qt6 6.6 REQUIRED COMPONENTS Core Gui Quick QuickControls2 QuickDialogs2)
find_package(KF6 6.0 REQUIRED COMPONENTS I18n Kirigami)

add_subdirectory(src)

cmake_minimum_required(VERSION 3.20)
project(fubuki-qt VERSION 0.2.3)

find_package(ECM 6.0 REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})
include(KDEInstallDirs)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)

find_package(Qt6 6.6 REQUIRED COMPONENTS Core Gui Widgets Quick QuickControls2 QuickDialogs2)
find_package(KF6 6.0 REQUIRED COMPONENTS I18n Kirigami)

add_subdirectory(src)

# The .po files live at fubuki-qt/po, a sibling of this directory, so that
# a checkout can be pointed at with -S app the way the PKGBUILD does. Turns
# each po/<lang>/fubuki-qt.po into share/locale/<lang>/LC_MESSAGES/fubuki-qt.mo.
ki18n_install(../po)

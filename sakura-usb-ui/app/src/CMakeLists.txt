add_executable(sakura-usb-ui main.cpp backend.cpp resources.qrc)

target_link_libraries(sakura-usb-ui PRIVATE
    Qt6::Core Qt6::Gui Qt6::Quick Qt6::QuickControls2 Qt6::QuickDialogs2
    KF6::I18n
)

install(TARGETS sakura-usb-ui ${KDE_INSTALL_TARGETS_DEFAULT_ARGS})

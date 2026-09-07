import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// The last thing between the user and an erased drive.
QQC2.Dialog {
    id: dlg
    modal: true
    anchors.centerIn: parent
    width: Math.min(parent.width - Kirigami.Units.largeSpacing * 2, Kirigami.Units.gridUnit * 26)
    title: "Sakura USB Writer"
    standardButtons: QQC2.Dialog.Ok | QQC2.Dialog.Cancel

    property string deviceText: ""
    property string sizeText: ""
    property var job: ({})
    signal done(var job)

    function open(newJob) {
        job = newJob
        visible = true
    }
    onAccepted: dlg.done(job)

    contentItem: ColumnLayout {
        spacing: Kirigami.Units.largeSpacing
        RowLayout {
            spacing: Kirigami.Units.largeSpacing
            Kirigami.Icon {
                source: "dialog-warning"
                Layout.preferredWidth: Kirigami.Units.iconSizes.large
                Layout.preferredHeight: Kirigami.Units.iconSizes.large
                Layout.alignment: Qt.AlignTop
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font.bold: true
                text: "WARNING: ALL DATA ON DEVICE '" + dlg.deviceText + "' WILL BE DESTROYED."
            }
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: "Device: " + dlg.deviceText + (dlg.sizeText ? "\nSize: " + dlg.sizeText : "")
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: "To continue with this operation, click OK. To quit click CANCEL."
        }
    }
}

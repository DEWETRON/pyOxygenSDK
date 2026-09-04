import QtQuick 2.3
import QtQuick.Controls 1.2 as QtControls
import QtQuick.Layouts 1.1
import Oxygen 1.0
import Oxygen.Dialogs 1.0
import Oxygen.Layouts 1.0
import Oxygen.Themes 1.0
import Oxygen.Tools 1.0
import Oxygen.Widgets 1.0

Item
{
    id: root

    property var channels: QObjectTreeModel {}

    property string filename
    property bool csv_valid: false
    readonly property bool settingsValid: filename !== "" && csv_valid

    function queryProperties()
    {
        var props = plugin.createPropertyList();
        props.setString("ODKEX_REPLAY_SYNC_SCALAR_PY/CSVFile", root.filename);
        return props;
    }

    ColumnLayout
    {
        anchors.leftMargin: Theme.smallMargin
        anchors.rightMargin: Theme.smallMargin
        anchors.fill: parent
        spacing: Theme.mediumSpacing

        Button {
            id: fileButton
            text: qsTranslate("ODKEX_REPLAY_SYNC_SCALAR_PY/AddChannel",
                              "Select CSV file") + Theme.actionEllipsis
            onClicked: { fileDialog.open(); }
        }

        Label {
            text: qsTranslate("ODKEX_REPLAY_SYNC_SCALAR_PY/AddChannel",
                              "Selected CSV file") + ":"
        }

        TextField {
            id: idInputField
            Layout.fillWidth: true
            text: root.filename
            readOnly: true
            placeholderText: qsTranslate("ODKEX_REPLAY_SYNC_SCALAR_PY/AddChannel",
                                         "No file selected")
        }

        Label {
            text: qsTranslate("ODKEX_REPLAY_SYNC_SCALAR_PY/AddChannel",
                              "Not a valid CSV file")
            visible: !root.csv_valid
            color: Theme.error
        }

        VerticalSpacer {}
    }

    CustomPluginRequest {
        id: verifyCSVFile
        messageId: 1

        function startCheck(path) {
            var props = plugin.createPropertyList();
            props.setString("filename", path);
            request(props);
        }
        onResponse: {
            root.csv_valid = value.getBool("valid");
        }
    }

    FileDialog {
        id: fileDialog
        selectExisting: true
        nameFilters: ["CSV Files (*.csv)"]
        title: qsTranslate("ODKEX_REPLAY_SYNC_SCALAR_PY/AddChannel",
                           "Open CSV file")
        onAccepted: {
            var path = fileUrl.toString().substr(8);
            root.filename = path;
            root.csv_valid = false;
            verifyCSVFile.startCheck(path);
        }
        onRejected: {}
    }
}

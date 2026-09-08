#include "backend.h"

#include <KLocalizedString>

#include <QFileInfo>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickStyle>

int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    // The application name doubles as the QSettings file the Windows dialog
    // remembers its choices in, so it is the package name rather than the
    // engine's.
    app.setApplicationName(QStringLiteral("fubuki-ui"));
    app.setOrganizationName(QStringLiteral("Fubuki"));
    app.setDesktopFileName(QStringLiteral("io.github.dresden196.fubuki"));
    QQuickStyle::setStyle(QStringLiteral("org.kde.desktop"));
    KLocalizedString::setApplicationDomain(QByteArrayLiteral("fubuki-ui"));

    // An image given on the command line -- an ISO opened with the writer
    // from a file manager. Handed to the window as the boot selection, and
    // nothing more: opening a file must never start writing a drive.
    QString openFile;
    const QStringList args = app.arguments();
    for (int i = 1; i < args.size(); ++i) {
        if (args.at(i).startsWith(QStringLiteral("-"))) {
            continue;
        }
        const QFileInfo info(args.at(i));
        if (info.isFile()) {
            openFile = info.absoluteFilePath();
        }
    }

    Backend backend;
    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("openFile"), openFile);
    engine.rootContext()->setContextProperty(QStringLiteral("backend"), &backend);
    engine.load(QUrl(QStringLiteral("qrc:/qml/Main.qml")));
    return engine.rootObjects().isEmpty() ? 1 : app.exec();
}

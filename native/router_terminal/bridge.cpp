#include "KodoTerm/KodoTerm.hpp"
#include "PtyProcess.h"
#include <QApplication>
#include <QClipboard>
#include <QVariant>
using WriteCallback=void(*)(const char*,int);
using ResizeCallback=void(*)(int,int);
class ExternalTransport final : public PtyProcess {
public:
    ExternalTransport(QObject* parent):PtyProcess(parent){}
    bool start(const QSize& size) override {resize(size);return true;}
    void write(const QByteArray& data) override {
        auto callback=reinterpret_cast<WriteCallback>(parent()->property("wb_write").value<quintptr>());
        if(callback)callback(data.constData(),int(data.size()));
    }
    void resize(const QSize& size) override {
        auto callback=reinterpret_cast<ResizeCallback>(parent()->property("wb_resize").value<quintptr>());
        if(callback)callback(size.width(),size.height());
    }
    void kill() override {}
    bool isRoot() const override {return false;}
    QString foregroundProcessName() const override {return {};}
};
bool PtyProcess::start(const QString& program,const QStringList& arguments,const QSize& size){setProgram(program);setArguments(arguments);return start(size);}
PtyProcess* PtyProcess::create(QObject* parent){return new ExternalTransport(parent);}
#define EXPORT extern "C" __declspec(dllexport)
EXPORT QWidget* wb_create(QWidget* parent,WriteCallback write,ResizeCallback resize){
    auto* view=new KodoTerm(parent);
    view->setProperty("wb_write",QVariant::fromValue(reinterpret_cast<quintptr>(write)));
    view->setProperty("wb_resize",QVariant::fromValue(reinterpret_cast<quintptr>(resize)));
    auto config=view->getConfig();config.enableLogging=false;config.maxScrollback=2000;
    config.pasteOnMiddleClick=false;config.copyOnSelect=false;
    config.font=QFont("Consolas",12);view->setConfig(config);
    view->setProgram("winbox-external-transport");view->start();return view;
}
EXPORT void wb_feed(KodoTerm* view,const char* data,int size){view->onPtyReadyRead(QByteArray(data,size));}
EXPORT int wb_text(KodoTerm* view,char* data,int size){
    auto* clipboard=QApplication::clipboard();auto previous=clipboard->text();
    view->selectAll();view->copyToClipboard();auto text=clipboard->text().toUtf8();clipboard->setText(previous);
    if(size>0){int count=qMin(size-1,int(text.size()));memcpy(data,text.constData(),count);data[count]=0;}
    return int(text.size());
}
EXPORT void wb_reset(KodoTerm* view){view->resetTerminal();}
EXPORT void wb_paste(KodoTerm* view){view->pasteFromClipboard();}

EXPORT const char* wb_qt_version(){return QT_VERSION_STR;}
EXPORT int wb_read(KodoTerm* view,int selected,char* data,int size){
    auto text=(selected?view->selectedText():view->allText()).toUtf8();
    if(size>0){int count=qMin(size-1,int(text.size()));memcpy(data,text.constData(),count);data[count]=0;}return int(text.size());
}
EXPORT int wb_selected(KodoTerm* view){return view->hasSelection()?1:0;}
EXPORT void wb_clear_selection(KodoTerm* view){view->clearSelection();}
EXPORT void wb_paste_text(KodoTerm* view,const char* data,int size){view->pasteText(QString::fromUtf8(data,size));}
EXPORT void wb_font(KodoTerm* view,int pixels){view->setFontPixels(pixels);}
EXPORT int wb_find(KodoTerm* view,const char* data,int size){return view->findNext(QString::fromUtf8(data,size))?1:0;}
EXPORT int wb_link(KodoTerm* view,int x,int y,char* data,int size){
    auto text=view->linkAt(QPoint(x,y)).toUtf8();int count=qMin(size-1,int(text.size()));if(size>0){memcpy(data,text.constData(),count);data[count]=0;}return int(text.size());
}

EXPORT void wb_select_all(KodoTerm* view){view->selectAll();}
EXPORT void wb_clear(KodoTerm* view){view->clearScrollback();view->onPtyReadyRead(QByteArray("\x1b[2J\x1b[H"));view->clearSelection();}
EXPORT void wb_detach(KodoTerm* view){view->setProperty("wb_write",QVariant::fromValue(quintptr(0)));view->setProperty("wb_resize",QVariant::fromValue(quintptr(0)));}

EXPORT void wb_colors(KodoTerm* view,unsigned int foreground,unsigned int background){
    auto config=view->getConfig();config.theme.foreground=QColor::fromRgba(foreground);config.theme.background=QColor::fromRgba(background);view->setConfig(config);
}

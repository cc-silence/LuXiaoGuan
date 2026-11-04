"""Present verified hcx candidate decisions with the existing common dialog."""
from PySide6.QtCore import QObject,Qt
from PySide6.QtWidgets import QApplication
from modules.common_widgets.common_dialogs import ConfirmDialog


def candidate_text(review):
    return ('\u5019\u9009\u5305\u5df2\u901a\u8fc7\u6765\u6e90\u7d22\u5f15\u6821\u9a8c\uff0c\u7248\u672c\u548c\u67b6\u6784\u4e00\u81f4\uff0c\u53ef\u4ee5\u5c1d\u8bd5\u5b89\u88c5\uff1b\u8be5\u6784\u5efa\u5c1a\u672a\u771f\u673a\u9a8c\u8bc1\u3002\n'
            + '\u5931\u8d25\u540e\u53ef\u5c1d\u8bd5\u5378\u8f7d hcx \u672c\u4f53\uff0c\u4f46\u5378\u8f7d\u4e0d\u7b49\u4e8e\u5b8c\u6574\u56de\u6eda\u3002\n\n'
            + '\u7248\u672c\uff1a'+review.version+'\n'
            + '\u8bbe\u5907\u8f6f\u4ef6\u5305\u7c7b\u578b\uff1a'+review.architecture+'\n'
            + '\u4e0b\u8f7d\u6765\u6e90\uff1a'+review.source+'\n'
            + '\u9884\u8bbe\u539f\u5305\uff1a'+str(review.original_size)+' \u5b57\u8282\n'
            + '\u5019\u9009\u5305\uff1a'+str(review.candidate_size)+' \u5b57\u8282\n\n'
            + '\u6587\u4ef6\u6307\u7eb9\u4e0e\u9884\u8bbe\u539f\u5305\u4e0d\u540c\u3002\u786e\u8ba4\u540e\u5b89\u88c5 hcx \u672c\u4f53\u5e76\u590d\u6d4b\uff0c\u4fdd\u7559\u5df2\u6709\u516c\u5171\u8fd0\u884c\u5e93\u7248\u672c\u3002\n'
            + '\u672c\u6b21\u9009\u62e9\u4ec5\u9002\u7528\u4e8e\u8fd9\u4e2a\u6587\u4ef6\uff0c\u4e0d\u4f1a\u8bb0\u4f4f\u5e76\u81ea\u52a8\u5141\u8bb8\u5176\u4ed6\u5305\u3002')


class DependencyCandidatePrompt(QObject):
    def __init__(self,window,gate):
        super().__init__(window)
        self.window=window
        self.gate=gate
        self.dialog=None
        self.request=None
        self.disposed=False
        gate.available=True
        gate.requested.connect(self._show,Qt.QueuedConnection)
        gate.closed.connect(self._closed,Qt.QueuedConnection)
        window.destroyed.connect(self.dispose)

    def _show(self,request):
        if self.disposed or self.gate.pending is not request or request.completed.is_set():return
        if not self.window.isVisible():
            request.answer(False)
            return
        parent=QApplication.activeModalWidget() or self.window
        dialog=None
        self.request=request
        try:
            dialog=ConfirmDialog('\u786e\u8ba4\u5c1d\u8bd5 hcx \u5019\u9009\u6784\u5efa',candidate_text(request.review),parent,
                                 yes_text='\u5c1d\u8bd5\u5b89\u88c5',no_text='\u8df3\u8fc7 hcx')
            dialog.no_button.setFocus()
            self.dialog=dialog
            accepted=dialog.exec()==ConfirmDialog.Accepted
            request.answer(accepted)
        except Exception:
            import logging
            logging.getLogger('routermanager.dependency').exception('Could not present candidate confirmation')
            request.answer(False,'\u5019\u9009\u5305\u786e\u8ba4\u7a97\u53e3\u672a\u80fd\u6253\u5f00\uff0c\u672a\u5b89\u88c5 hcx\u3002')
        finally:
            self.dialog=None
            self.request=None
            if dialog is not None:dialog.deleteLater()

    def _closed(self,request):
        if self.request is request and self.dialog is not None:self.dialog.reject()

    def dispose(self,*args):
        if self.disposed:return
        self.disposed=True
        self.gate.available=False
        self.gate.cancel_pending()
        if self.dialog is not None:self.dialog.reject()
        self.gate.requested.disconnect(self._show)
        self.gate.closed.disconnect(self._closed)


def ensure_candidate_prompt(page,service):
    gate=getattr(service,'package_confirmation',None)
    from modules.capture_services.dependency_confirmation import PackageConfirmationGate
    if not isinstance(gate,PackageConfirmationGate):return None
    window=page.window()
    prompt=getattr(window,'_dependency_candidate_prompt',None)
    if prompt is not None and not prompt.disposed and prompt.gate is gate:return prompt
    if prompt is not None:prompt.dispose()
    prompt=DependencyCandidatePrompt(window,gate)
    window._dependency_candidate_prompt=prompt
    return prompt

"""Route shared action feedback through its owning page's presentation contract."""


def publish_page_message(page,text,level="info"):
    message=getattr(page,'_message',None)
    if callable(message):message(text,level)
    else:page.status_label.setText(text)

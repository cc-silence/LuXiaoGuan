"""Dynamic input error state using the common dialog theme."""

def set_input_error(edit,invalid):
    invalid=bool(invalid)
    if edit.property('inputError')==invalid:return
    edit.setProperty('inputError',invalid)
    style=edit.style();style.unpolish(edit);style.polish(edit);edit.update()

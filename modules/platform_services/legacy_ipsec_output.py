"""Strict command-output decoding; every candidate still needs full policy verification."""
import locale


def text_candidates(data):
    if not isinstance(data,bytes) or len(data)>4*1024*1024:
        raise ValueError('IPsec dump exceeds byte limit')
    if data.startswith((b'\xff\xfe',b'\xfe\xff')):
        yield data.decode('utf-16',errors='strict')
        return
    encodings=['utf-8-sig']
    encodings.extend(native_code_pages())
    encodings.append(locale.getpreferredencoding(False))
    if len(data)%2==0 and b'\0' in data[:256]:encodings.append('utf-16-le')
    for encoding in dict.fromkeys(encodings):
        try:text=data.decode(encoding,errors='strict')
        except UnicodeError:continue
        yield text


def native_code_pages():
    from .runtime import is_windows
    if not is_windows():return []
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    encodings=[]
    for name in ('GetACP','GetOEMCP'):
        function=getattr(kernel,name)
        function.argtypes=[]
        function.restype=wintypes.UINT
        code=function()
        if code:encodings.append('cp'+str(code))
    return encodings

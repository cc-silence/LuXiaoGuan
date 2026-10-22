import os
import time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from tests.test_standalone import APP
from PySide6.QtGui import QImageWriter
from router_app.runtime import RouterRuntime
from router_app.window import RouterWindow
runtime=RouterRuntime('1.0.0_alpha',APP)
for key,value in [('theme',os.environ.get('ROUTERMANAGER_PREVIEW_THEME','light.qss')),('scale','100'),('font_family','default')]:runtime.config.set('ui',key,value)
window=RouterWindow(runtime,'1.0.0_alpha')
window.show();APP.processEvents()
image=window.grab().toImage()
writer=QImageWriter(str(Path(__file__).parent/('router-'+os.environ.get('ROUTERMANAGER_PREVIEW_THEME','light.qss')+'.png')),b'png')
writer.setCompression(0)
assert writer.write(image),writer.errorString()
print('Preview saved',flush=True)
runtime.exit_application()
deadline=time.monotonic()+10
while not runtime._ready_to_close and time.monotonic()<deadline:APP.processEvents();time.sleep(.01)
assert runtime._ready_to_close

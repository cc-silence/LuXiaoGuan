import unittest
from threading import Event
from unittest.mock import Mock, patch
from dataclasses import replace
from tests.test_dependency_diagnostic_paths import Owner
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState


class CurlInstallFeedbackTests(unittest.TestCase):
    def test_failed_post_install_probe_reports_real_reason(self):
        owner=Owner(); service=OpenWrtDependencyService(owner)
        broken=DependencyInfo('curl','curl',DependencyState.UNKNOWN,detail='curl_multi_get_offt missing')
        service.manager.install_all=Mock(return_value=(broken,))
        with patch('modules.capture_services.dependency_service.check_tools',return_value=(broken,)):
            self.assertTrue(service.install_async({'curl':True},scope='curl'))
            operation,event=owner.pending
            result=operation(event,Mock())
        self.assertIn('curl_multi_get_offt missing',result['error'])
        service._complete(service.task.session_id,result)

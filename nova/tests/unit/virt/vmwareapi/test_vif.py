# Copyright 2013 Canonical Corp.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from unittest import mock

from oslo_vmware import vim_util

from nova import exception
from nova.network import model as network_model
from nova import test
from nova.tests.unit import utils
from nova.tests.unit.virt.vmwareapi import fake
from nova.virt.vmwareapi import constants
from nova.virt.vmwareapi import network_util
from nova.virt.vmwareapi import vif


class VMwareVifTestCase(test.NoDBTestCase):
    def setUp(self):
        super(VMwareVifTestCase, self).setUp()
        network = network_model.Network(id=0,
                                        bridge='fa0',
                                        label='fake',
                                        vlan=3,
                                        bridge_interface='eth0',
                                        injected=True)
        self._network = network
        self.vif = network_model.NetworkInfo([
                network_model.VIF(id=None,
                                  address='DE:AD:BE:EF:00:00',
                                  network=network,
                                  type=None,
                                  devname=None,
                                  ovs_interfaceid=None,
                                  rxtx_cap=3)
        ])[0]
        self.session = fake.FakeSession()
        self.cluster = None

    def test_get_vif_info_none(self):
        vif_info = vif.get_vif_info('fake_session', 'fake_cluster',
                                    'fake_model', None)
        self.assertEqual([], vif_info)

    def test_get_vif_info_empty_list(self):
        vif_info = vif.get_vif_info('fake_session', 'fake_cluster',
                                    'fake_model', [])
        self.assertEqual([], vif_info)

    @mock.patch.object(vif, 'get_network_ref', return_value='fake_ref')
    def test_get_vif_info(self, mock_get_network_ref):
        network_info = utils.get_test_network_info()
        vif_info = vif.get_vif_info('fake_session', 'fake_cluster',
                                    'fake_model', network_info)
        expected = [{'iface_id': utils.FAKE_VIF_UUID,
                     'mac_address': utils.FAKE_VIF_MAC,
                     'network_name': utils.FAKE_NETWORK_BRIDGE,
                     'network_ref': 'fake_ref',
                     'vif_model': 'fake_model'}]
        self.assertEqual(expected, vif_info)

    @mock.patch.object(vif, '_check_ovs_supported_version')
    def test_get_network_ref_ovs_integration_bridge(self, mock_check):
        self.flags(integration_bridge='fake-bridge-id', group='vmware')
        vif_info = network_model.NetworkInfo([
                network_model.VIF(type=network_model.VIF_TYPE_OVS,
                                  address='DE:AD:BE:EF:00:00',
                                  network=self._network)]
        )[0]
        network_ref = vif.get_network_ref('fake-session',
                                          'fake-cluster',
                                          vif_info)
        expected_ref = {'type': 'OpaqueNetwork',
                        'network-id': 'fake-bridge-id',
                        'network-type': 'opaque',
                        'use-external-id': False}
        self.assertEqual(expected_ref, network_ref)
        mock_check.assert_called_once_with('fake-session')

    @mock.patch.object(vif, '_check_ovs_supported_version')
    def test_get_network_ref_ovs(self, mock_check):
        vif_info = network_model.NetworkInfo([
                network_model.VIF(type=network_model.VIF_TYPE_OVS,
                                  address='DE:AD:BE:EF:00:00',
                                  network=self._network)]
        )[0]
        network_ref = vif.get_network_ref('fake-session',
                                          'fake-cluster',
                                          vif_info)
        expected_ref = {'type': 'OpaqueNetwork',
                        'network-id': 0,
                        'network-type': 'nsx.LogicalSwitch',
                        'use-external-id': True}
        self.assertEqual(expected_ref, network_ref)
        mock_check.assert_called_once_with('fake-session')

    @mock.patch.object(vif, '_check_ovs_supported_version')
    def test_get_network_ref_ovs_logical_switch_id(self, mock_check):
        vif_info = network_model.NetworkInfo([
                network_model.VIF(type=network_model.VIF_TYPE_OVS,
                                  address='DE:AD:BE:EF:00:00',
                                  network=self._network,
                                  details={'nsx-logical-switch-id':
                                           'fake-nsx-id'})]
        )[0]
        network_ref = vif.get_network_ref('fake-session',
                                          'fake-cluster',
                                          vif_info)
        expected_ref = {'type': 'OpaqueNetwork',
                        'network-id': 'fake-nsx-id',
                        'network-type': 'nsx.LogicalSwitch',
                        'use-external-id': True}
        self.assertEqual(expected_ref, network_ref)
        mock_check.assert_called_once_with('fake-session')

    @mock.patch.object(network_util, 'get_network_with_the_name')
    def test_get_network_ref_dvs(self, mock_network_name):
        fake_network_obj = {'type': 'DistributedVirtualPortgroup',
                            'dvpg': 'fake-key',
                            'dvsw': 'fake-props'}
        mock_network_name.return_value = fake_network_obj
        vif_info = network_model.NetworkInfo([
                network_model.VIF(type=network_model.VIF_TYPE_DVS,
                                  address='DE:AD:BE:EF:00:00',
                                  network=self._network)]
        )[0]
        network_ref = vif.get_network_ref('fake-session',
                                          'fake-cluster',
                                          vif_info)
        mock_network_name.assert_called_once_with('fake-session',
                                                  'fa0',
                                                  'fake-cluster')
        self.assertEqual(fake_network_obj, network_ref)

    @mock.patch.object(network_util, 'get_network_with_the_name')
    def test_get_network_ref_dvs_vif_details(self, mock_network_name):
        fake_network_obj = {'type': 'DistributedVirtualPortgroup',
                            'dvpg': 'pg1',
                            'dvsw': 'fake-props'}
        mock_network_name.return_value = fake_network_obj
        vif_info = network_model.NetworkInfo([
                network_model.VIF(type=network_model.VIF_TYPE_DVS,
                                  details={'dvs_port_key': 'key1',
                                           'dvs_port_group_name': 'pg1'},
                                  address='DE:AD:BE:EF:00:00',
                                  network=self._network)])[0]
        network_ref = vif.get_network_ref('fake-session',
                                          'fake-cluster',
                                          vif_info)
        mock_network_name.assert_called_once_with('fake-session',
                                                  'pg1',
                                                  'fake-cluster')
        self.assertEqual(fake_network_obj, network_ref)

    @mock.patch.object(network_util, 'get_network_with_the_name',
                       return_value=None)
    def test_get_network_ref_dvs_no_match(self, mock_network_name):
        vif_info = network_model.NetworkInfo([
                network_model.VIF(type=network_model.VIF_TYPE_DVS,
                                  address='DE:AD:BE:EF:00:00',
                                  network=self._network)]
        )[0]
        self.assertRaises(exception.NetworkNotFoundForBridge,
                          vif.get_network_ref,
                          'fake-session',
                          'fake-cluster',
                          vif_info)

    def test_get_network_ref_invalid_type(self):
        vif_info = network_model.NetworkInfo([
                network_model.VIF(address='DE:AD:BE:EF:00:00',
                                  network=self._network)]
        )[0]
        self.assertRaises(exception.InvalidInput,
                          vif.get_network_ref,
                          'fake-session',
                          'fake-cluster',
                          vif_info)

    @mock.patch.object(vif.LOG, 'warning')
    @mock.patch.object(vim_util, 'get_vc_version',
                       return_value='5.0.0')
    def test_check_invalid_ovs_version(self, mock_version, mock_warning):
        vif._check_ovs_supported_version('fake_session')
        # assert that the min version is in a warning message
        expected_arg = {'version': constants.MIN_VC_OVS_VERSION}
        version_arg_found = False
        for call in mock_warning.call_args_list:
            if call[0][1] == expected_arg:
                version_arg_found = True
                break
        self.assertTrue(version_arg_found)

    @mock.patch.object(network_util, 'get_network_with_the_name')
    def test_get_network_ref_dvs_provider(self, mock_network_name):
        fake_network_obj = {'type': 'DistributedVirtualPortgroup',
                            'dvpg': 'fake-key',
                            'dvsw': 'fake-props'}
        mock_network_name.side_effect = [fake_network_obj]
        vif_info = network_model.NetworkInfo([
                network_model.VIF(type=network_model.VIF_TYPE_DVS,
                                  address='DE:AD:BE:EF:00:00',
                                  network=self._network)]
        )[0]
        network_ref = vif.get_network_ref('fake-session',
                                          'fake-cluster',
                                          vif_info)
        calls = [mock.call('fake-session', 'fa0', 'fake-cluster')]
        mock_network_name.assert_has_calls(calls)
        self.assertEqual(fake_network_obj, network_ref)

    @mock.patch.object(network_util, 'get_network_with_the_name',
                       return_value=None)
    def test_raise_neutron_network_dvs(self, mock_network_name):
        mock_network_name.side_effect = None
        vif_info = network_model.NetworkInfo([
            network_model.VIF(type=network_model.VIF_TYPE_DVS,
                              address='DE:AD:BE:EF:00:00',
                              network=self._network)]
        )[0]

        self.assertRaises(exception.NetworkNotFoundForBridge,
                          vif.get_network_ref,
                          'fake-session',
                          'fake-cluster',
                          vif_info)

    @mock.patch.object(network_util, 'get_network_with_the_name')
    def test_get_network_ref_dvs_with_dvs_pg_id(self, mock_network_name):
        fake_network_obj = {'type': 'DistributedVirtualPortgroup',
                            'dvpg': 'fake-key',
                            'dvsw': 'fake-props'}
        mock_network_name.side_effect = [fake_network_obj]
        vif_details = {
                'dvs_id': 'fake-props',
                'pg_id': 'fake-key'
        }
        vif_info = network_model.NetworkInfo([
            network_model.VIF(type=network_model.VIF_TYPE_DVS,
                              address='DE:AD:BE:EF:00:00',
                              network=self._network,
                              details=vif_details)]
        )[0]
        network_ref = vif.get_network_ref('fake-session',
                                          'fake-cluster',
                                          vif_info)

        fake_network_ref = {'type': 'DistributedVirtualPortgroup',
                            'dvpg': 'fake-key',
                            'dvsw': 'fake-props'}

        self.assertEqual(network_ref, fake_network_ref)
        calls = []
        mock_network_name.assert_has_calls(calls)
        self.assertEqual(fake_network_obj, network_ref)

    def test_get_supported_vif_model_with_set(self):
        """Test that the first supported model from a set is returned."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_supported_vif_models': {'e1000', 'vmxnet3', 'e1000e'}
            }
        })
        # vmxnet3 is first in SUPPORTED_VIF_MODELS
        result = vif._get_supported_vif_model(image_meta)
        self.assertEqual('vmxnet3', result)

    def test_get_supported_vif_model_priority_order(self):
        """Test that models are selected in priority order."""
        from nova import objects
        # Only e1000 is in both the set and SUPPORTED_VIF_MODELS
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_supported_vif_models': {'e1000', 'rtl8139'}
            }
        })
        result = vif._get_supported_vif_model(image_meta)
        self.assertEqual('e1000', result)

    def test_get_supported_vif_model_no_match(self):
        """Test that None is returned when no model matches."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_supported_vif_models': {'rtl8139', 'ne2k_pci'}
            }
        })
        result = vif._get_supported_vif_model(image_meta)
        self.assertIsNone(result)

    def test_get_supported_vif_model_no_property(self):
        """Test that None is returned when property is not set."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({'properties': {}})
        result = vif._get_supported_vif_model(image_meta)
        self.assertIsNone(result)

    def test_get_supported_vif_model_none_image_meta(self):
        """Test that None is returned when image_meta is None."""
        result = vif._get_supported_vif_model(None)
        self.assertIsNone(result)

    def test_get_supported_vif_model_empty_set(self):
        """Test that None is returned for empty set."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_supported_vif_models': set()
            }
        })
        result = vif._get_supported_vif_model(image_meta)
        self.assertIsNone(result)

    def test_get_vif_model_with_supported_models(self):
        """Test get_vif_model with hw_supported_vif_models."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_supported_vif_models': {'e1000', 'vmxnet3'}
            }
        })
        result = vif.get_vif_model(image_meta)
        # vmxnet3 should be selected as it's first in priority
        self.assertEqual('vmxnet3', result)

    def test_get_vif_model_with_hw_vif_model(self):
        """Test get_vif_model falls back to hw_vif_model."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_vif_model': 'e1000e'
            }
        })
        result = vif.get_vif_model(image_meta)
        self.assertEqual('e1000e', result)

    def test_get_vif_model_supported_takes_priority(self):
        """Test hw_supported_vif_models takes priority over hw_vif_model."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_supported_vif_models': {'vmxnet3'},
                'hw_vif_model': 'e1000'
            }
        })
        result = vif.get_vif_model(image_meta)
        # hw_supported_vif_models should take priority
        self.assertEqual('vmxnet3', result)

    def test_get_vif_model_no_match_falls_back_to_hw_vif_model(self):
        """Test fallback to hw_vif_model when no supported model matches."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_supported_vif_models': {'rtl8139'},  # Not supported
                'hw_vif_model': 'e1000'
            }
        })
        result = vif.get_vif_model(image_meta)
        self.assertEqual('e1000', result)

    def test_get_vif_model_default(self):
        """Test get_vif_model returns default when no properties set."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({'properties': {}})
        result = vif.get_vif_model(image_meta)
        self.assertEqual(constants.DEFAULT_VIF_MODEL, result)

    def test_get_vif_model_none_image_meta(self):
        """Test get_vif_model returns default when image_meta is None."""
        result = vif.get_vif_model(None)
        self.assertEqual(constants.DEFAULT_VIF_MODEL, result)

    def test_get_vif_model_no_properties(self):
        """Test get_vif_model returns default when properties missing."""
        from nova import objects
        # Image meta without properties attribute
        image_meta = objects.ImageMeta.from_dict({})
        result = vif.get_vif_model(image_meta)
        self.assertEqual(constants.DEFAULT_VIF_MODEL, result)

    def test_get_vif_model_empty_supported_set_with_hw_vif_model(self):
        """Test empty hw_supported_vif_models falls back to hw_vif_model."""
        from nova import objects
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_supported_vif_models': set(),
                'hw_vif_model': 'vmxnet'
            }
        })
        result = vif.get_vif_model(image_meta)
        self.assertEqual('vmxnet', result)

    def test_get_vif_model_priority_selection(self):
        """Test that get_vif_model respects priority order."""
        from nova import objects
        # Provide models in reverse priority order
        image_meta = objects.ImageMeta.from_dict({
            'properties': {
                'hw_supported_vif_models': {'e1000', 'e1000e', 'vmxnet3'}
            }
        })
        result = vif.get_vif_model(image_meta)
        # vmxnet3 should be selected as highest priority
        self.assertEqual('vmxnet3', result)

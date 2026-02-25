# Copyright (c) 2014 VMware, Inc.
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

"""
Test suite for images.
"""

import os
import tarfile
from unittest import mock

from oslo_utils.fixture import uuidsentinel as uuids
from oslo_utils import units
from oslo_vmware import rw_handles

import nova.conf
from nova import exception
from nova import objects
from nova import test
from nova.virt.vmwareapi import constants
from nova.virt.vmwareapi import images
from nova.virt.vmwareapi import vm_util

CONF = nova.conf.CONF


class VMwareImagesTestCase(test.NoDBTestCase):
    """Unit tests for Vmware API connection calls."""

    def test_fetch_image(self):
        """Test fetching images."""

        dc_name = 'fake-dc'
        file_path = 'fake_file'
        ds_name = 'ds1'
        host = mock.MagicMock()
        port = 7443
        context = mock.MagicMock()

        image_data = {
            'id': uuids.image,
            'disk_format': 'vmdk',
            'size': 512,
        }
        read_file_handle = mock.MagicMock()
        write_file_handle = mock.MagicMock()
        read_iter = mock.MagicMock()
        instance = objects.Instance(id=1,
                                    uuid=uuids.foo,
                                    image_ref=image_data['id'])

        def fake_read_handle(read_iter):
            return read_file_handle

        def fake_write_handle(host, port, dc_name, ds_name, cookies,
                              file_path, file_size):
            return write_file_handle

        with test.nested(
             mock.patch.object(rw_handles, 'ImageReadHandle',
                               side_effect=fake_read_handle),
             mock.patch.object(rw_handles, 'FileWriteHandle',
                               side_effect=fake_write_handle),
             mock.patch.object(images, 'image_transfer'),
             mock.patch.object(images.IMAGE_API, 'get',
                return_value=image_data),
             mock.patch.object(images.IMAGE_API, 'download',
                     return_value=read_iter),
        ) as (glance_read, http_write, image_transfer, image_show,
                image_download):
            images.fetch_image(context, instance,
                               host, port, dc_name,
                               ds_name, file_path)

        glance_read.assert_called_once_with(read_iter)
        http_write.assert_called_once_with(host, port, dc_name, ds_name, None,
                                           file_path, image_data['size'])
        image_transfer.assert_called_once_with(read_file_handle,
                                               write_file_handle)
        image_download.assert_called_once_with(context, instance['image_ref'])
        image_show.assert_called_once_with(context, instance['image_ref'])

    def _setup_mock_get_remote_image_service(self,
                                             mock_get_remote_image_service,
                                             metadata):
        mock_image_service = mock.MagicMock()
        mock_image_service.show.return_value = metadata
        mock_get_remote_image_service.return_value = [mock_image_service, 'i']

    def test_get_vmdk_name_from_ovf(self):
        ovf_path = os.path.join(os.path.dirname(__file__), 'ovf.xml')
        with open(ovf_path) as f:
            ovf_descriptor = f.read()
            vmdk_name = images.get_vmdk_name_from_ovf(ovf_descriptor)
            self.assertEqual("Damn_Small_Linux-disk1.vmdk", vmdk_name)

    @mock.patch('oslo_vmware.rw_handles.ImageReadHandle')
    @mock.patch('oslo_vmware.rw_handles.VmdkWriteHandle')
    @mock.patch.object(tarfile, 'open')
    def test_fetch_image_ova(self, mock_tar_open, mock_write_class,
                             mock_read_class):
        session = mock.MagicMock()
        ovf_descriptor = None
        ovf_path = os.path.join(os.path.dirname(__file__), 'ovf.xml')
        with open(ovf_path) as f:
            ovf_descriptor = f.read()

        with test.nested(
             mock.patch.object(images.IMAGE_API, 'get'),
             mock.patch.object(images.IMAGE_API, 'download'),
             mock.patch.object(images, 'image_transfer'),
             mock.patch.object(images, '_build_shadow_vm_config_spec'),
             mock.patch.object(vm_util, 'get_vmdk_info')
        ) as (mock_image_api_get,
              mock_image_api_download,
              mock_image_transfer,
              mock_build_shadow_vm_config_spec,
              mock_get_vmdk_info):
            image_data = {'id': 'fake-id',
                          'disk_format': 'vmdk',
                          'size': 512}
            instance = mock.MagicMock()
            instance.image_ref = image_data['id']
            mock_image_api_get.return_value = image_data

            vm_folder_ref = mock.MagicMock()
            res_pool_ref = mock.MagicMock()
            context = mock.MagicMock()

            mock_read_handle = mock.MagicMock()
            mock_read_class.return_value = mock_read_handle
            mock_write_handle = mock.MagicMock()
            mock_write_class.return_value = mock_write_handle
            mock_write_handle.get_imported_vm.return_value = \
                mock.sentinel.vm_ref

            mock_ovf = mock.MagicMock()
            mock_ovf.name = 'dsl.ovf'
            mock_vmdk = mock.MagicMock()
            mock_vmdk.name = "Damn_Small_Linux-disk1.vmdk"

            def fake_extract(name):
                if name == mock_ovf:
                    m = mock.MagicMock()
                    m.read.return_value = ovf_descriptor
                    return m
                elif name == mock_vmdk:
                    return mock_read_handle

            mock_tar = mock.MagicMock()
            mock_tar.__iter__ = mock.Mock(return_value = iter([mock_ovf,
                                                               mock_vmdk]))
            mock_tar.extractfile = fake_extract
            mock_tar_open.return_value.__enter__.return_value = mock_tar

            images.fetch_image_ova(
                    context, instance, session, 'fake-vm', 'fake-datastore',
                    vm_folder_ref, res_pool_ref)

            mock_tar_open.assert_called_once_with(mode='r|',
                                                  fileobj=mock_read_handle)
            mock_image_transfer.assert_called_once_with(mock_read_handle,
                                                        mock_write_handle)
            mock_get_vmdk_info.assert_called_once_with(
                    session, mock.sentinel.vm_ref)
            session._call_method.assert_called_once_with(
                    session.vim, "MarkAsTemplate", mock.sentinel.vm_ref)

    @mock.patch('oslo_vmware.rw_handles.ImageReadHandle')
    @mock.patch('oslo_vmware.rw_handles.VmdkWriteHandle')
    def test_fetch_image_stream_optimized(self,
                                          mock_write_class,
                                          mock_read_class):
        """Test fetching streamOptimized disk image."""
        session = mock.MagicMock()
        CONF.set_override('allow_pulling_images_from_url', False,
                          'vmware')

        with test.nested(
             mock.patch.object(images.IMAGE_API, 'get'),
             mock.patch.object(images.IMAGE_API, 'download'),
             mock.patch.object(images, 'image_transfer'),
             mock.patch.object(images, '_build_shadow_vm_config_spec'),
             mock.patch.object(vm_util, 'get_vmdk_info')
        ) as (mock_image_api_get,
              mock_image_api_download,
              mock_image_transfer,
              mock_build_shadow_vm_config_spec,
              mock_get_vmdk_info):
            image_data = {'id': 'fake-id',
                          'disk_format': 'vmdk',
                          'size': 512}
            instance = mock.MagicMock()
            instance.image_ref = image_data['id']
            mock_image_api_get.return_value = image_data

            vm_folder_ref = mock.MagicMock()
            res_pool_ref = mock.MagicMock()
            context = mock.MagicMock()

            mock_read_handle = mock.MagicMock()
            mock_read_class.return_value = mock_read_handle
            mock_write_handle = mock.MagicMock()
            mock_write_class.return_value = mock_write_handle
            mock_write_handle.get_imported_vm.return_value = \
                mock.sentinel.vm_ref

            images.fetch_image_stream_optimized(
                    context, instance, session, 'fake-vm', 'fake-datastore',
                    vm_folder_ref, res_pool_ref)

            mock_image_transfer.assert_called_once_with(mock_read_handle,
                                                        mock_write_handle)
            session._call_method.assert_called_once_with(
                    session.vim, "MarkAsTemplate", mock.sentinel.vm_ref)
            mock_get_vmdk_info.assert_called_once_with(
                    session, mock.sentinel.vm_ref)

    def test_from_image_with_image_ref(self):
        raw_disk_size_in_gb = 83
        raw_disk_size_in_bytes = raw_disk_size_in_gb * units.Gi
        mdata = {'size': raw_disk_size_in_bytes,
                 'disk_format': 'vmdk',
                 'owner': '',
                 'properties': {
                     "vmware_ostype": constants.DEFAULT_OS_TYPE,
                     "vmware_adaptertype": constants.DEFAULT_ADAPTER_TYPE,
                     "vmware_disktype": constants.DEFAULT_DISK_TYPE,
                     "hw_vif_model": constants.DEFAULT_VIF_MODEL,
                     "vmware_linked_clone": True}}
        mdata = objects.ImageMeta.from_dict(mdata)
        with mock.patch.object(
            images, 'get_vsphere_location', return_value=None,
        ):
            img_props = images.VMwareImage.from_image(None, uuids.image, mdata)

        image_size_in_kb = raw_disk_size_in_bytes // units.Ki

        # assert that defaults are set and no value returned is left empty
        self.assertEqual(constants.DEFAULT_OS_TYPE, img_props.os_type)
        self.assertEqual(constants.DEFAULT_ADAPTER_TYPE,
                         img_props.adapter_type)
        self.assertEqual(constants.DEFAULT_DISK_TYPE, img_props.disk_type)
        self.assertEqual(constants.DEFAULT_VIF_MODEL, img_props.vif_model)
        self.assertTrue(img_props.linked_clone)
        self.assertEqual(image_size_in_kb, img_props.file_size_in_kb)

    def _image_build(self, image_lc_setting, global_lc_setting,
                     disk_format=constants.DEFAULT_DISK_FORMAT,
                     os_type=constants.DEFAULT_OS_TYPE,
                     adapter_type=constants.DEFAULT_ADAPTER_TYPE,
                     disk_type=constants.DEFAULT_DISK_TYPE,
                     vif_model=constants.DEFAULT_VIF_MODEL,
                     vsphere_location=None):
        self.flags(use_linked_clone=global_lc_setting, group='vmware')
        raw_disk_size_in_gb = 93
        raw_disk_size_in_btyes = raw_disk_size_in_gb * units.Gi

        mdata = {'size': raw_disk_size_in_btyes,
                 'disk_format': disk_format,
                 'owner': '',
                 'properties': {
                     "vmware_ostype": os_type,
                     "vmware_adaptertype": adapter_type,
                     "vmware_disktype": disk_type,
                     "hw_vif_model": vif_model}}

        if image_lc_setting is not None:
            mdata['properties']["vmware_linked_clone"] = image_lc_setting

        context = mock.Mock()
        mdata = objects.ImageMeta.from_dict(mdata)
        with mock.patch.object(
            images, 'get_vsphere_location', return_value=vsphere_location,
        ):
            return images.VMwareImage.from_image(context, uuids.image, mdata)

    def test_use_linked_clone_override_nf(self):
        image_props = self._image_build(None, False)
        self.assertFalse(image_props.linked_clone,
                         "No overrides present but still overridden!")

    def test_use_linked_clone_override_nt(self):
        image_props = self._image_build(None, True)
        self.assertTrue(image_props.linked_clone,
                        "No overrides present but still overridden!")

    def test_use_linked_clone_override_ny(self):
        image_props = self._image_build(None, "yes")
        self.assertTrue(image_props.linked_clone,
                        "No overrides present but still overridden!")

    def test_use_linked_clone_override_ft(self):
        image_props = self._image_build(False, True)
        self.assertFalse(image_props.linked_clone,
                         "image level metadata failed to override global")

    def test_use_linked_clone_override_string_nt(self):
        image_props = self._image_build("no", True)
        self.assertFalse(image_props.linked_clone,
                         "image level metadata failed to override global")

    def test_use_linked_clone_override_string_yf(self):
        image_props = self._image_build("yes", False)
        self.assertTrue(image_props.linked_clone,
                        "image level metadata failed to override global")

    def test_use_disk_format_iso(self):
        image = self._image_build(None, True, disk_format='iso')
        self.assertEqual('iso', image.file_type)
        self.assertTrue(image.is_iso)

    def test_use_bad_disk_format(self):
        self.assertRaises(exception.InvalidDiskFormat,
                          self._image_build,
                          None,
                          True,
                          disk_format='bad_disk_format')

    def test_image_no_defaults(self):
        image = self._image_build(False, False,
                                  disk_format='iso',
                                  os_type='otherGuest',
                                  adapter_type='lsiLogic',
                                  disk_type='preallocated',
                                  vif_model='e1000e')
        self.assertEqual('iso', image.file_type)
        self.assertEqual('otherGuest', image.os_type)
        self.assertEqual('lsiLogic', image.adapter_type)
        self.assertEqual('preallocated', image.disk_type)
        self.assertEqual('e1000e', image.vif_model)
        self.assertFalse(image.linked_clone)

    def test_image_defaults(self):
        image = images.VMwareImage(image_id='fake-image-id')

        # N.B. We intentially don't use the defined constants here. Amongst
        # other potential failures, we're interested in changes to their
        # values, which would not otherwise be picked up.
        self.assertEqual('otherGuest', image.os_type)
        self.assertEqual('lsiLogic', image.adapter_type)
        self.assertEqual('preallocated', image.disk_type)
        self.assertEqual('e1000e', image.vif_model)

    def test_use_vsphere_location(self):
        image = self._image_build(None, True, vsphere_location='vsphere://ok')
        self.assertEqual('vsphere://ok', image.vsphere_location)

    def test_get_vsphere_location(self):
        expected = 'vsphere://ok'
        metadata = {'locations': [{}, {'url': 'http://ko'}, {'url': expected}]}
        with mock.patch.object(images.IMAGE_API, 'get', return_value=metadata):
            context = mock.Mock()
            observed = images.get_vsphere_location(context, 'image_id')
            self.assertEqual(expected, observed)

    def test_get_no_vsphere_location(self):
        metadata = {'locations': [{}, {'url': 'http://ko'}]}
        with mock.patch.object(images.IMAGE_API, 'get', return_value=metadata):
            context = mock.Mock()
            observed = images.get_vsphere_location(context, 'image_id')
            self.assertIsNone(observed)

    def test_get_vsphere_location_no_image(self):
        context = mock.Mock()
        observed = images.get_vsphere_location(context, None)
        self.assertIsNone(observed)

    # Tests for hw_supported_scsi_models functionality

    def _build_image_meta_with_properties(self, properties_dict):
        """Helper to build ImageMeta object from properties dict."""
        mdata = {
            'size': 93 * units.Gi,
            'disk_format': 'vmdk',
            'owner': '',
            'properties': properties_dict
        }
        return objects.ImageMeta.from_dict(mdata)

    def test_supported_scsi_models_driver_preference(self):
        """Test selecting based on driver preference order."""
        properties = {
            'hw_disk_bus': 'scsi',
            'hw_supported_scsi_models': set(['lsisas1068', 'vmpvscsi']),
        }
        image_meta = self._build_image_meta_with_properties(properties)

        with mock.patch.object(images, 'get_vsphere_location',
                               return_value=None):
            image = images.VMwareImage.from_image(None, uuids.image,
                                                  image_meta)

        # Should select vmpvscsi (driver's most preferred that image supports)
        self.assertEqual(constants.ADAPTER_TYPE_PARAVIRTUAL,
                        image.adapter_type)

    def test_supported_scsi_models_driver_prefers_vmpvscsi(self):
        """Test driver prefers vmpvscsi over buslogic."""
        properties = {
            'hw_disk_bus': 'scsi',
            'hw_supported_scsi_models': set(['buslogic', 'vmpvscsi']),
        }
        image_meta = self._build_image_meta_with_properties(properties)

        with mock.patch.object(images, 'get_vsphere_location',
                               return_value=None):
            image = images.VMwareImage.from_image(None, uuids.image,
                                                  image_meta)

        # Should select vmpvscsi (driver's most preferred)
        self.assertEqual(constants.ADAPTER_TYPE_PARAVIRTUAL,
                        image.adapter_type)

    def test_supported_scsi_models_fallback_to_hw_scsi_model(self):
        """Test fallback to hw_scsi_model when no matching models."""
        properties = {
            'hw_disk_bus': 'scsi',
            'hw_supported_scsi_models': set(),
            'hw_scsi_model': 'vmpvscsi',
        }
        image_meta = self._build_image_meta_with_properties(properties)

        with mock.patch.object(images, 'get_vsphere_location',
                               return_value=None):
            image = images.VMwareImage.from_image(None, uuids.image,
                                                  image_meta)

        # Should fall back to hw_scsi_model
        self.assertEqual(constants.ADAPTER_TYPE_PARAVIRTUAL,
                        image.adapter_type)

    def test_supported_scsi_models_priority_over_hw_scsi_model(self):
        """Test hw_supported_scsi_models takes priority over hw_scsi_model."""
        properties = {
            'hw_disk_bus': 'scsi',
            'hw_supported_scsi_models': set(['lsilogic', 'buslogic']),
            'hw_scsi_model': 'vmpvscsi',
        }
        image_meta = self._build_image_meta_with_properties(properties)

        with mock.patch.object(images, 'get_vsphere_location',
                               return_value=None):
            image = images.VMwareImage.from_image(None, uuids.image,
                                                  image_meta)

        # Should use lsilogic from hw_supported_scsi_models (driver's
        # preference), not hw_scsi_model
        self.assertEqual(constants.DEFAULT_ADAPTER_TYPE, image.adapter_type)

    def test_supported_scsi_models_all_valid_types(self):
        """Test all valid SCSI model types."""
        test_cases = [
            ('lsilogic', constants.DEFAULT_ADAPTER_TYPE),
            ('lsisas1068', constants.ADAPTER_TYPE_LSILOGICSAS),
            ('buslogic', constants.ADAPTER_TYPE_BUSLOGIC),
            ('vmpvscsi', constants.ADAPTER_TYPE_PARAVIRTUAL),
        ]

        for scsi_model, expected_adapter in test_cases:
            properties = {
                'hw_disk_bus': 'scsi',
                'hw_supported_scsi_models': set([scsi_model]),
            }
            image_meta = self._build_image_meta_with_properties(properties)

            with mock.patch.object(images, 'get_vsphere_location',
                                   return_value=None):
                image = images.VMwareImage.from_image(None, uuids.image,
                                                      image_meta)

            self.assertEqual(expected_adapter, image.adapter_type,
                           f"Failed for SCSI model: {scsi_model}")

    def test_supported_scsi_models_with_supported_disk_buses(self):
        """Test hw_supported_scsi_models with explicit SCSI disk bus."""
        properties = {
            'hw_disk_bus': 'scsi',
            'hw_supported_scsi_models': set(['vmpvscsi', 'lsilogic']),
        }
        image_meta = self._build_image_meta_with_properties(properties)

        with mock.patch.object(images, 'get_vsphere_location',
                               return_value=None):
            image = images.VMwareImage.from_image(None, uuids.image,
                                                  image_meta)

        # When SCSI bus is used, driver prefers vmpvscsi over lsilogic
        self.assertEqual(constants.ADAPTER_TYPE_PARAVIRTUAL,
                        image.adapter_type)

    def test_supported_scsi_models_no_match(self):
        """Test behavior when no SCSI models match driver preferences."""
        properties = {
            'hw_disk_bus': 'scsi',
            'hw_supported_scsi_models': set(),
        }
        image_meta = self._build_image_meta_with_properties(properties)

        with mock.patch.object(images, 'get_vsphere_location',
                               return_value=None):
            image = images.VMwareImage.from_image(None, uuids.image,
                                                  image_meta)

        # Should have None adapter_type (mapping.get(None) returns None)
        self.assertIsNone(image.adapter_type)

    def test_supported_scsi_models_empty_set(self):
        """Test behavior with empty hw_supported_scsi_models set."""
        properties = {
            'hw_disk_bus': 'scsi',
            'hw_supported_scsi_models': set(),
            'hw_scsi_model': 'buslogic',
        }
        image_meta = self._build_image_meta_with_properties(properties)

        with mock.patch.object(images, 'get_vsphere_location',
                               return_value=None):
            image = images.VMwareImage.from_image(None, uuids.image,
                                                  image_meta)

        # Should fall back to hw_scsi_model
        self.assertEqual(constants.ADAPTER_TYPE_BUSLOGIC, image.adapter_type)

    def test_supported_scsi_models_not_set(self):
        """Test behavior when hw_supported_scsi_models is not set."""
        properties = {
            'hw_disk_bus': 'scsi',
            'hw_scsi_model': 'lsisas1068',
        }
        image_meta = self._build_image_meta_with_properties(properties)

        with mock.patch.object(images, 'get_vsphere_location',
                               return_value=None):
            image = images.VMwareImage.from_image(None, uuids.image,
                                                  image_meta)

        # Should use hw_scsi_model as before
        self.assertEqual(constants.ADAPTER_TYPE_LSILOGICSAS,
                        image.adapter_type)

    def test_supported_scsi_models_ide_bus_ignored(self):
        """Test hw_supported_scsi_models ignored when IDE bus selected."""
        properties = {
            'hw_disk_bus': 'ide',
            'hw_supported_scsi_models': set(['vmpvscsi']),
        }
        image_meta = self._build_image_meta_with_properties(properties)

        with mock.patch.object(images, 'get_vsphere_location',
                               return_value=None):
            image = images.VMwareImage.from_image(None, uuids.image,
                                                  image_meta)

        # IDE adapter should be used, SCSI models ignored
        self.assertEqual(constants.ADAPTER_TYPE_IDE, image.adapter_type)

    def test_get_scsi_model_from_image_properties_direct(self):
        """Test _get_scsi_model_from_image_properties function directly."""
        properties_dict = {
            'hw_supported_scsi_models': set(['buslogic', 'lsilogic']),
            'hw_scsi_model': 'vmpvscsi',
        }
        image_meta = self._build_image_meta_with_properties(properties_dict)
        properties = image_meta.properties

        result = images._get_scsi_model_from_image_properties(properties)

        # Should return lsilogic (driver's preference over buslogic)
        self.assertEqual('lsilogic', result)

    def test_get_scsi_model_fallback_direct(self):
        """Test fallback to hw_scsi_model in helper function."""
        properties_dict = {
            'hw_supported_scsi_models': set(),
            'hw_scsi_model': 'lsilogic',
        }
        image_meta = self._build_image_meta_with_properties(properties_dict)
        properties = image_meta.properties

        result = images._get_scsi_model_from_image_properties(properties)

        # Should fall back to hw_scsi_model
        self.assertEqual('lsilogic', result)

    def test_get_scsi_model_none_set(self):
        """Test when neither supported models nor hw_scsi_model set."""
        properties_dict = {}
        image_meta = self._build_image_meta_with_properties(properties_dict)
        properties = image_meta.properties

        result = images._get_scsi_model_from_image_properties(properties)

        # Should return None
        self.assertIsNone(result)

{
  pkgs,
  nixosModules,
  novaPkg,
  generateRootwrapConf,
  chvModule,
}:
let
  nova_env = pkgs.python3.buildEnv.override {
    extraLibs = [ novaPkg ];
  };
  execDirs = pkgs.buildEnv {
    name = "utils";
    paths = [ nova_env ];
  };
  rootwrapConf = generateRootwrapConf {
    package = nova_env;
    filterPath = "/etc/nova/rootwrap.d";
    execDirs = execDirs;
  };
in
pkgs.nixosTest {
  name = "OpenStack Cloud Hypervisor driver test";

  nodes.controllerVM =
    { ... }:
    {
      imports = [
        nixosModules.controllerModule
        nixosModules.testModules.testController
      ];

      nova.novaPackage = novaPkg;
    };

  nodes.computeVM =
    { config, ... }:
    {
      imports = [
        nixosModules.computeModule
        nixosModules.testModules.testCompute
        chvModule
      ];

      nova.novaPackage = novaPkg;
      nova.extraPkgs = [ pkgs.cloud-hypervisor ];

      nova.config = pkgs.writeText "nova.conf" ''
        [DEFAULT]
        log_dir = /var/log/nova
        lock_path = /var/lock/nova
        state_path = /var/lib/nova
        rootwrap_config = ${rootwrapConf}
        compute_driver = libvirt.LibvirtDriver
        my_ip = 10.0.0.39
        transport_url = rabbit://openstack:openstack@controller

        [api]
        auth_strategy = keystone

        [api_database]
        connection = sqlite:////var/lib/nova/nova_api.sqlite

        [database]
        connection = sqlite:////var/lib/nova/nova.sqlite

        [glance]
        api_servers = http://controller:9292

        [keystone_authtoken]
        www_authenticate_uri = http://controller:5000/
        auth_url = http://controller:5000/
        memcached_servers = controller:11211
        auth_type = password
        project_domain_name = Default
        user_domain_name = Default
        project_name = service
        username = nova
        password = nova

        [libvirt]
        virt_type = ch
        images_type = raw

        [neutron]
        auth_url = http://controller:5000
        auth_type = password
        project_domain_name = Default
        user_domain_name = Default
        region_name = RegionOne
        project_name = service
        username = neutron
        password = neutron

        [os_vif_ovs]
        ovsdb_connection = unix:/run/openvswitch/db.sock

        [oslo_concurrency]
        lock_path = /var/lib/nova/tmp

        [placement]
        region_name = RegionOne
        project_domain_name = Default
        project_name = service
        auth_type = password
        user_domain_name = Default
        auth_url = http://controller:5000/v3
        username = placement
        password = placement

        [service_user]
        send_service_user_token = true
        auth_url = http://controller:5000/
        auth_strategy = keystone
        auth_type = password
        project_domain_name = Default
        project_name = service
        user_domain_name = Default
        username = nova
        password = nova

        [vnc]
        enabled = true
        server_listen = 0.0.0.0
        server_proxyclient_address = $my_ip
        novncproxy_base_url = http://controller:6080/vnc_lite.html

        [cells]
        enable = False

        [os_region_name]
        openstack =
      '';
    };

  testScript =
    { ... }:
    ''
      import json
      import time

      def retry_until_succeed(machine, cmd, retries = 10):
        for i in range(retries):
          print(f"Retrying command until success '{cmd}'. {i + 1}/{retries} retries")
          time.sleep(1)
          status, _ = machine.execute(cmd)
          if status == 0:
            return True
        return False

      def print_logfile(machine, filepath):
        _, out = controllerVM.execute(f"cat {filepath}")
        print(f"Printing log of: {filepath}")
        print(out)

      def wait_for_openstack():
        for i in range(120):
          print(f"Waiting for openstack network agents and compute nodes to be present ... {i + 1}/120 sec")
          time.sleep(1)
          status, out = controllerVM.execute("openstack network agent list -f json")
          if status != 0:
            continue
          net_agents = json.loads(out)
          status, out = controllerVM.execute("openstack compute service list --service nova-compute -f json")
          if status != 0:
            continue
          compute_nodes = json.loads(out)
          if len(net_agents) == 4 and len(compute_nodes) == 1:
            return True
        return False

      def wait_for_openstack_vm():
        for i in range(30):
          print(f"Waiting for openstack server to be active ... {i + 1}/30 sec")
          time.sleep(1)
          status, out = controllerVM.execute("openstack server list -f json")
          if status != 0:
            continue
          vms = json.loads(out)
          if len(vms) == 1 and vms[0]["Status"] == "ACTIVE":
            return True
          elif len(vms) == 1 and vms[0]["Status"] == "ERROR":
            print(out)
            print_logfile(controllerVM, "/var/log/nova/.nova-manage-wrapped.log")
            print_logfile(controllerVM, "/var/log/nova/.nova-scheduler-wrapped.log")
            print_logfile(controllerVM, "/var/log/nova/.nova-api-wrapped.log")
            print_logfile(controllerVM, "/var/log/nova/.nova-conductor-wrapped.log")
            print_logfile(controllerVM, "/var/log/neutron/.neutron-server-wrapped.log")
            print_logfile(controllerVM, "/var/log/neutron/.neutron-openvswitch-agent-wrapped.log")
            print_logfile(controllerVM, "/var/log/neutron/.neutron-dhcp-agent-wrapped.log")
            return False
        return False

      def wait_for_network_namespace():
        for i in range(30):
          print(f"Waiting for network namespace to appear ... {i +1}/30 sec")
          time.sleep(1)
          net_ns = controllerVM.succeed("ip netns list | awk '{ print $1 }'").strip()
          if net_ns != "":
            return net_ns
        return ""


      start_all()
      controllerVM.wait_for_unit("glance-api.service")
      controllerVM.wait_for_unit("placement-api.service")
      controllerVM.wait_for_unit("neutron-server.service")
      controllerVM.wait_for_unit("nova-scheduler.service")
      controllerVM.wait_for_unit("nova-conductor.service")

      assert wait_for_openstack()

      controllerVM.succeed("systemctl start nova-host-discovery.service")
      controllerVM.wait_for_unit("nova-host-discovery.service")
      controllerVM.succeed("systemctl start openstack-create-vm.service")
      controllerVM.wait_for_unit("openstack-create-vm.service")
      assert wait_for_openstack_vm()

      # Check that our Cloud Hypervisor driver is loaded and correctly reported
      assert retry_until_succeed(controllerVM, "openstack hypervisor list")
      hypervisor_list = json.loads(controllerVM.succeed("openstack hypervisor list -f json"))
      assert hypervisor_list[0]["Hypervisor Type"] == "CH"

      computeVM.succeed("pgrep -f cloud-hypervisor")

      vm_state = json.loads(controllerVM.succeed("openstack server show test_vm -f json"))

      vm_ip = vm_state["addresses"]["provider"][0]
      assert vm_ip.startswith("192.168.44")

      net_ns = wait_for_network_namespace()
      assert net_ns != ""

      # Ping the OpenStack VM from the controller host. We use the network
      # namespace dedicated for the VM to ping it.
      assert retry_until_succeed(controllerVM, f"ip netns exec {net_ns} ping -c 1 {vm_ip}", 30)

      # Test that deletion and cleanup works as expected
      controllerVM.succeed("openstack server delete test_vm --wait")

      assert retry_until_succeed(controllerVM, "openstack server list")
      server_list = json.loads(controllerVM.succeed("openstack server list -f json"))
      assert len(server_list) == 0

      computeVM.fail("pgrep -f cloud-hypervisor")
    '';
}

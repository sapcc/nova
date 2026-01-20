{
  nixosTest,
  nixosModules,
  generateRootwrapConf,
  novaPkg,
  chvModule,
  testScriptFile,
}:
let
  novaConfigForIp =
    ip:
    # NixOS module:
    { config, pkgs, ... }:
    {
      nova.novaPackage = novaPkg;

      nova.config =
        let
          nova_env = pkgs.python3.buildEnv.override {
            extraLibs = [ config.nova.novaPackage ];
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
        pkgs.writeText "nova.conf" ''
          [DEFAULT]
          compute_driver = libvirt.LibvirtDriver
          lock_path = /var/lock/nova
          log_dir = /var/log/nova
          my_ip = ${ip}
          rootwrap_config = ${rootwrapConf}
          state_path = /var/lib/nova
          transport_url = rabbit://openstack:openstack@controller

          [api]
          auth_strategy = keystone

          [api_database]
          connection = sqlite:////var/lib/nova/nova_api.sqlite

          [cells]
          enable = False

          [database]
          connection = sqlite:////var/lib/nova/nova.sqlite

          [glance]
          api_servers = http://controller:9292

          [keystone_authtoken]
          auth_type = password
          auth_url = http://controller:5000/
          memcached_servers = controller:11211
          password = nova
          project_domain_name = Default
          project_name = service
          user_domain_name = Default
          username = nova
          www_authenticate_uri = http://controller:5000/

          [libvirt]
          images_type = raw
          virt_type = ch

          [neutron]
          auth_type = password
          auth_url = http://controller:5000
          password = neutron
          project_domain_name = Default
          project_name = service
          region_name = RegionOne
          user_domain_name = Default
          username = neutron

          [os_region_name]
          openstack =

          [os_vif_ovs]
          ovsdb_connection = unix:/run/openvswitch/db.sock

          [oslo_concurrency]
          lock_path = /var/lib/nova/tmp

          [placement]
          auth_type = password
          auth_url = http://controller:5000/v3
          password = placement
          project_domain_name = Default
          project_name = service
          region_name = RegionOne
          user_domain_name = Default
          username = placement

          [service_user]
          auth_strategy = keystone
          auth_type = password
          auth_url = http://controller:5000/
          password = nova
          project_domain_name = Default
          project_name = service
          send_service_user_token = true
          user_domain_name = Default
          username = nova

          [vnc]
          enabled = true
          novncproxy_base_url = http://controller:6080/vnc_lite.html
          server_listen = 0.0.0.0
          server_proxyclient_address = $my_ip
        '';
    };
in
nixosTest {
  name = "OpenStack bdf live migration test";

  nodes.controllerVM =
    { pkgs, ... }:
    {
      imports = [
        nixosModules.controllerModule
        nixosModules.testModules.testController
      ];

      environment.systemPackages = [
        pkgs.sshpass
      ];

    };

  nodes.computeVM =
    { ... }:
    {
      imports = [
        nixosModules.computeModule
        nixosModules.testModules.testCompute
        chvModule
        (novaConfigForIp "10.0.0.39")
      ];

      environment.variables = {
        LIBVIRT_DEFAULT_URI = "ch:///system";
      };

      networking.extraHosts = ''
        10.0.0.40 computeVM2 computeVM2.local
      '';
    };

  nodes.computeVM2 =
    { pkgs, lib, ... }:
    {
      imports = [
        nixosModules.computeModule
        nixosModules.testModules.testCompute
        chvModule
        (novaConfigForIp "10.0.0.40")
      ];

      environment.variables = {
        LIBVIRT_DEFAULT_URI = "ch:///system";
      };

      networking.extraHosts = ''
        10.0.0.39 computeVM computeVM.local
      '';

      systemd.network.networks.eth1.networkConfig.Address = lib.mkForce "10.0.0.40/24";
    };

  testScript = { ... }: builtins.readFile testScriptFile;
}

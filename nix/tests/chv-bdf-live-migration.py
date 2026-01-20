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
        if len(net_agents) == 5 and len(compute_nodes) == 2:
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

def wait_vm_ssh_reachable(controllerVM, vm_ip, net_ns):
    return retry_until_succeed(controllerVM, f"ip netns exec {net_ns} sshpass -p gocubsgo ssh cirros@{vm_ip} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null lspci", 60)

def get_lspci_output(controllerVM, vm_ip, net_ns):
    return controllerVM.succeed(f"ip netns exec {net_ns} sshpass -p gocubsgo ssh cirros@{vm_ip} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null lspci")

def parse_devices_from_dom_def(xml_input: str):
    """
    Parses `devices` from a domain XML given by `xml_input` returns them in a dict.
    The dict returned contains the device PCI slot as keys and an identification string of a device as value. The string
    differs between device types.
    :param xml_input: xml input string
    :return: dict[str, str] = ['<PCI slot in hex>' : '<info:about:device>']
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_input)
    result = dict()
    # Use ".//devices" because in persistent conf, `devices` is direct child and in transient config it's one more level
    # of nesting.
    for device in root.find(".//devices") or []:
        value = ""
        value += device.tag
        match device.tag:
            case "disk":
                source = device.find("source")
                if source is not None:
                    value += ":" + source.get("file", "").strip()
                target = device.find("target")
                if target is not None:
                    value += ":" + target.get("dev", "").strip()
            case "interface":
                value += ":" + device.attrib.get("type", "")
                mac = device.find("mac")
                if mac is not None:
                    value += ":" + mac.get("address", "")
                target = device.find("target")
                if target is not None:
                    value += ":" + target.get("dev", "").strip()
            case "rng":
                backend = device.find("backend")
                if backend is not None:
                    value += ":" + (backend.text or "").strip()
        address = device.find("address")
        if address is not None:
            if address.get("type") == "pci":
                result[address.get("slot")] = value
    return result


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

vm_state = json.loads(controllerVM.succeed("openstack server show test_vm -f json"))

src_host = vm_state["OS-EXT-SRV-ATTR:host"]
dst_host = "computeVM2" if src_host == "computeVM" else "computeVM"

vm_ip = vm_state["addresses"]["provider"][0]
assert vm_ip.startswith("192.168.44")

net_ns = wait_for_network_namespace()
assert net_ns != ""

# check vm is online and reachable
assert retry_until_succeed(controllerVM, f"ip netns exec {net_ns} ping -c 1 {vm_ip}", 30)
assert wait_vm_ssh_reachable(controllerVM, vm_ip, net_ns)

#
current_host = computeVM if src_host == "computeVM" else computeVM2
first_boot_libvirt_config = parse_devices_from_dom_def(current_host.succeed("virsh dumpxml --domain instance-00000001"))
# get lspci output from first boot
first_boot_lspci = get_lspci_output(controllerVM, vm_ip, net_ns)

# reboot VM
print(f"Reboot on host: {src_host}")
controllerVM.succeed("openstack server reboot test_vm")

# check vm is online and reachable
assert wait_for_openstack_vm()
assert wait_vm_ssh_reachable(controllerVM, vm_ip, net_ns)

second_boot_libvirt_config = parse_devices_from_dom_def(current_host.succeed("virsh dumpxml --domain instance-00000001"))
second_boot_lspci = get_lspci_output(controllerVM, vm_ip, net_ns)

# check xml output
print("Compare xml content first boot - after first reboot")
assert first_boot_libvirt_config == second_boot_libvirt_config

# check lspci output
print("Compare lspci output first boot - after first reboot")
assert first_boot_lspci == second_boot_lspci

# live migration
print(f"Start migration from src: {src_host} to destination {dst_host}")
controllerVM.succeed(f"openstack server migrate --live-migration --host {dst_host} test_vm")

# check vm is online and reachable
assert wait_for_openstack_vm()
assert wait_vm_ssh_reachable(controllerVM, vm_ip, net_ns)

vm_state = json.loads(controllerVM.succeed("openstack server show test_vm -f json"))
src_host = vm_state["OS-EXT-SRV-ATTR:host"]
current_host = computeVM if src_host == "computeVM" else computeVM2

post_migration_libvirt_config = parse_devices_from_dom_def(current_host.succeed("virsh dumpxml --domain instance-00000001"))
post_migration_lspci = get_lspci_output(controllerVM, vm_ip, net_ns)

# check xml output
print("Compare xml content first boot - post live migration")
assert first_boot_libvirt_config == post_migration_libvirt_config

# check lspci output
print("Compare lspci output first boot - post live migration")
assert first_boot_lspci == post_migration_lspci

image_id = 'ab0c0912-dd0e-4f96-b428-c8742a0ac969'
flavour_id = 'ae0e555f-688a-40e3-ab40-b27bb506b94d'
key_pair = 'rdp'
network_id = '58877b05-70db-4c49-8413-8cbd1aa6ad51'
security_group_name = 'prod-sec'
from datetime import datetime


def generate_payload():
    network = dict()
    security_group = dict()
    server = dict()

    network['uuid'] = network_id
    security_group['name'] = security_group_name
    current_time = str(datetime.now()).replace(" ","_")

    vm_name = "vm-"+current_time

    server['name'] = vm_name
    server['imageRef'] = image_id
    server['flavorRef'] = flavour_id
    server['key_name'] = key_pair
    server['networks'] = [network]
    server['security_groups'] = [security_group]

    payload = dict()
    payload['server'] = server

    return payload

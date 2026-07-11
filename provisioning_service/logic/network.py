import httpx

external_network_id = 'c2e7fc3d-ef57-459b-b251-205291635588'

def get_port_to_device(base_url:str,x_auth_token:str,device_id:str):
    with httpx.Client() as client:
        response = client.get(
            f"{base_url}/servers/{device_id}/os-interface",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()

def create_floating_ip(base_url: str, x_auth_token: str, port_id: str):
    floating_ip = dict()
    floating_ip['floating_network_id'] = external_network_id
    floating_ip['port_id'] = port_id

    payload = dict()
    payload['floatingip'] = floating_ip
    with httpx.Client() as client:
        response =  client.post(
            f"{base_url}/v2.0/floatingips",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


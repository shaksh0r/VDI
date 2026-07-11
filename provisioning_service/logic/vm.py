import httpx


def create_instance_local_storage(base_url: str, x_auth_token: str, payload: dict):
    with httpx.Client() as client:
        response =  client.post(
            f"{base_url}/servers",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()

def get_detailed_instances(base_url: str, x_auth_token: str):
    with httpx.Client() as client:
        response = client.get(
            f"{base_url}/servers/detail",
            headers={"X-Auth-Token": x_auth_token}
        )
        return {
            "status_code": response.status_code,
            "data": response.json()
        }

def get_instance_detail(base_url: str, x_auth_token: str, instance_id: str):
    with httpx.Client() as client:
        response = client.get(
            f"{base_url}/servers/{instance_id}",
            headers={"X-Auth-Token": x_auth_token}
        )
        return {
            "status_code": response.status_code,
            "response": response.json()
        }
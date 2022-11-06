import json
import logging
import os

import azure.functions as func
import requests
from azure.storage.blob import BlobServiceClient


def get_devops_api_token():
    oauth2_headers = {"Content-Type": "application/x-www-form-urlencoded"}
    oauth2_body = {
        "client_id": os.environ["DEVOPS_CLIENT_ID"],
        "client_secret": os.environ["DEVOPS_CLIENT_SECRET"],
        "grant_type": "client_credentials",
        "resource": "https://management.azure.com",
    }
    oauth2_url = (
        f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}/oauth2/token"
    )
    try:
        return requests.post(
            url=oauth2_url, headers=oauth2_headers, data=oauth2_body
        ).json()["access_token"]

    except requests.exceptions.RequestException as e:
        raise SystemExit(e)


def get_api_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }


def main(req: func.HttpRequest) -> func.HttpResponse:
    # debugging start the function
    logging.info("------------------------------------------------")
    logging.info(f"{json.dumps((payload := req.get_json()), indent=4)}")
    logging.info("------------------------------------------------")

    # preparing variables
    (
        azure_bill_table_container_name,
        azure_bill_table_blob_name,
        devops_api_headers,
        rate,
        subscription_id,
    ) = (
        "azurebilltable",
        "azurebilltable.csv",
        get_api_headers(get_devops_api_token()),
        payload["Rate"],
        payload["SubscriptionId"],
    )
    tags = {
        "Department": payload["Department"],
        "Contact": payload["Contact"],
        "Application": payload["Application"],
        "ProjectCode": payload["ProjectCode"],
        "ProjectManager": payload["ProjectManager"],
        "BusinessUnit": payload["BusinessUnit"],
    }

    # applying policies
    for key, value in tags.items():
        body = {
            "identity": {"type": "SystemAssigned"},
            "location": "eastus",
            "properties": {
                "displayName": "Append a tag and its value to resources.",
                "description": "Appends the specified tag and value when any resource which is missing this tag is created or updated. Does not modify the tags of resources created before this policy was applied until those resources are changed. Does not apply to resource groups. New 'modify' effect policies are available that support remediation of tags on existing resources (see https://aka.ms/modifydoc).",
                "metadata": {"assignedBy": "sp-prod-devops"},
                "policyDefinitionId": "/providers/Microsoft.Authorization/policyDefinitions/2a0e14a6-b0a6-4fab-991a-187a4f81c498",
                "parameters": {
                    "tagName": {"value": f"{key}"},
                    "tagValue": {"value": f"{value}"},
                },
                "enforcementMode": "Default",
            },
        }
        try:
            response = requests.put(
                url=f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Authorization/policyAssignments/resource{key}?api-version=2020-09-01",
                headers=devops_api_headers,
                json=body,
            )
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)

        body = {
            "identity": {"type": "SystemAssigned"},
            "location": "eastus",
            "properties": {
                "displayName": "Append a tag and its value to resource groups.",
                "description": "Appends the specified tag and value when any resource group which is missing this tag is created or updated. Does not modify the tags of resource groups created before this policy was applied until those resource groups are changed. New 'modify' effect policies are available that support remediation of tags on existing resources (see https://aka.ms/modifydoc).",
                "metadata": {"assignedBy": "sp-prod-devops"},
                "policyDefinitionId": "/providers/Microsoft.Authorization/policyDefinitions/49c88fc8-6fd1-46fd-a676-f12d1d3a4c71",
                "parameters": {
                    "tagName": {"value": f"{key}"},
                    "tagValue": {"value": f"{value}"},
                },
                "enforcementMode": "Default",
            },
        }
        try:
            response = requests.put(
                url=f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Authorization/policyAssignments/resourcegroup{key}?api-version=2020-09-01",
                headers=devops_api_headers,
                json=body,
            )
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)

    # outputting result
    try:
        response = requests.get(
            url=f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Authorization/policyAssignments?api-version=2020-09-01",
            headers=devops_api_headers,
        )
    except requests.exceptions.RequestException as e:
        raise SystemExit(e)
    logging.info(json.dumps(response.json(), indent=4))

    # update billing reference table
    try:
        blob_service_client = BlobServiceClient.from_connection_string(
            os.environ["AZBILL_STORAGE_ACCOUNT_CONNECTION_STRING"]
        )
        blob_client = blob_service_client.get_blob_client(
            azure_bill_table_container_name, azure_bill_table_blob_name
        )
        # append new data to a str (table_data)
        table_data = (
            blob_client.download_blob().content_as_text(encoding="UTF-8")[1:]
            + (
                subscription_name := requests.get(
                    url=f"https://management.azure.com/subscriptions/{subscription_id}?api-version=2020-01-01",
                    headers=devops_api_headers,
                ).json()["displayName"]
            )
            + f",{rate},{tags['Department']},{tags['Contact']},{tags['Application']},{tags['ProjectCode']},{tags['ProjectManager']},{tags['BusinessUnit']}\r\n"
        )
        # delete the existing file table blob
        blob_client.delete_blob()

        # upload the exisintg file table blob
        blob_client.upload_blob(table_data)

        # email with new sub info
        requests.post(
            url=os.environ["AZUREPOLICY_LOGICAPP_URL"],
            json={
                "subscription_name": subscription_name,
                "rate": "Passthrough (Customer Self-Managed)"
                if rate == "1" and tags["Department"] != "Cenitex"
                else "Passthrough (Cenitex Owned)"
                if rate == "1" and tags["Department"] == "Cenitex"
                else "25% (Cenitex Managed)"
                if rate == "1.25"
                else "43.75% (Viccloudsafe Kofax)",
                "department": tags["Department"],
                "contact": tags["Contact"],
                "application": tags["Application"],
                "project_code": tags["ProjectCode"],
                "project_manager": tags["ProjectManager"],
                "business_unit": tags["BusinessUnit"],
            },
        )

    except Exception as e:
        logging.info(str(e))

    return func.HttpResponse(status_code=200)

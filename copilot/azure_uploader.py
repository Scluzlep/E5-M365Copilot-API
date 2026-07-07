import os
import json
import msal
import requests
import base64
import urllib.parse
from typing import Dict, Optional, Tuple

AZURE_CONFIG_PATH = os.path.join("sessions", "azure_config.json")

class AzureConfigManager:
    @classmethod
    def load_config(cls) -> dict:
        if not os.path.exists(AZURE_CONFIG_PATH):
            return {"azure_apps": {}, "session_mappings": {}}
        try:
            with open(AZURE_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"azure_apps": {}, "session_mappings": {}}

    @classmethod
    def save_config(cls, data: dict):
        os.makedirs(os.path.dirname(AZURE_CONFIG_PATH), exist_ok=True)
        with open(AZURE_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def get_session_info(cls, session_name: str) -> Optional[Tuple[dict, str]]:
        """Returns (azure_app_dict, user_upn) for a given session name."""
        config = cls.load_config()
        mapping = config.get("session_mappings", {}).get(session_name)
        if not mapping:
            return None
        
        azure_name = mapping.get("azure_name")
        upn = mapping.get("upn")
        
        if not azure_name or not upn:
            return None
            
        app_config = config.get("azure_apps", {}).get(azure_name)
        if not app_config:
            return None
            
        return app_config, upn

class AzureUploader:
    @classmethod
    def upload_file(cls, session_name: str, file_name: str, file_content: bytes, mime_type: str = "application/octet-stream") -> Optional[Dict]:
        """
        Uploads a file to the user's OneDrive under the 'copilot_files' directory.
        Returns a dictionary with 'id' and 'webUrl' if successful.
        """
        info = AzureConfigManager.get_session_info(session_name)
        if not info:
            print(f"[AzureUploader] No Azure mapping found for session '{session_name}'.")
            return None
            
        app_config, user_upn = info
        tenant_id = app_config.get("tenant_id")
        client_id = app_config.get("client_id")
        client_secret = app_config.get("client_secret")
        
        if not all([tenant_id, client_id, client_secret]):
            print(f"[AzureUploader] Incomplete Azure credentials for session '{session_name}'.")
            return None

        # 1. Get Access Token via MSAL
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=authority,
            client_credential=client_secret,
        )
        
        scopes = ["https://graph.microsoft.com/.default"]
        result = app.acquire_token_silent(scopes, account=None)
        if not result:
            result = app.acquire_token_for_client(scopes=scopes)
            
        if "access_token" not in result:
            print(f"[AzureUploader] Failed to get access token: {result.get('error_description')}")
            return None
            
        access_token = result["access_token"]
        
        # 2. Upload file to OneDrive via Microsoft Graph API using createUploadSession (Chunked Upload)
        encoded_file_name = urllib.parse.quote(file_name)
        create_session_url = f"https://graph.microsoft.com/v1.0/users/{user_upn}/drive/root:/copilot_files/{encoded_file_name}:/createUploadSession"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        session_payload = {
            "item": {
                "@microsoft.graph.conflictBehavior": "replace",
                "name": file_name
            }
        }
        
        try:
            # Create Upload Session
            session_resp = requests.post(create_session_url, headers=headers, json=session_payload)
            session_resp.raise_for_status()
            upload_url = session_resp.json().get("uploadUrl")
            
            if not upload_url:
                print(f"[AzureUploader] Failed to get uploadUrl: {session_resp.text}")
                return None
                
            # Upload chunks
            total_size = len(file_content)
            # Chunk size must be a multiple of 320 KiB (327,680 bytes)
            chunk_size = 327680 * 10  # ~3.125 MB
            
            uploaded_item = None
            
            for i in range(0, total_size, chunk_size):
                chunk = file_content[i:i + chunk_size]
                start = i
                end = i + len(chunk) - 1
                
                chunk_headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{total_size}"
                }
                
                chunk_resp = requests.put(upload_url, headers=chunk_headers, data=chunk)
                
                # HTTP 202 Accepted indicates chunk was uploaded but more remain
                # HTTP 201 Created or 200 OK indicates completion and returns the item
                if chunk_resp.status_code in (200, 201):
                    uploaded_item = chunk_resp.json()
                    break
                elif chunk_resp.status_code == 202:
                    continue
                else:
                    print(f"[AzureUploader] Chunk upload failed with status {chunk_resp.status_code}: {chunk_resp.text}")
                    return None
                    
            if not uploaded_item:
                print("[AzureUploader] Upload completed but no item data returned.")
                return None
                
            drive_id = uploaded_item.get('parentReference', {}).get('driveId', '')
            if drive_id.startswith('b!'):
                import base64, uuid
                b64_str = drive_id[2:]
                b64_str += '=' * (4 - len(b64_str) % 4)
                bytes_val = base64.urlsafe_b64decode(b64_str)
                if len(bytes_val) == 48:
                    guid1 = str(uuid.UUID(bytes_le=bytes_val[0:16]))
                    guid2 = str(uuid.UUID(bytes_le=bytes_val[16:32]))
                    guid3 = str(uuid.UUID(bytes_le=bytes_val[32:48]))
                    spo_str = f"{guid1},{guid2},{guid3}"
                    b64_spo = base64.urlsafe_b64encode(spo_str.encode()).decode().rstrip('=')
                    uploaded_item["spo_id"] = f"SPO_{b64_spo}_{uploaded_item.get('id')}"
            
            uploaded_item["tenant_id"] = tenant_id
            return uploaded_item
        except Exception as e:
            print(f"[AzureUploader] Upload failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(e.response.text)
            return None

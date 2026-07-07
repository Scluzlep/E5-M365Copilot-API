from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from copilot.azure_uploader import AzureConfigManager

router = APIRouter(prefix="/api/azure_config", tags=["azure_config"])

class AzureAppConfig(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: str = ""

class SessionMapping(BaseModel):
    azure_name: str
    upn: str

@router.post("/app/{azure_name}")
def set_azure_app(azure_name: str, config: AzureAppConfig):
    """Create or update an Azure App configuration. Write-only for secrets."""
    data = AzureConfigManager.load_config()
    if "azure_apps" not in data:
        data["azure_apps"] = {}
    
    existing_app = data["azure_apps"].get(azure_name, {})
    secret_to_save = config.client_secret
    if (not secret_to_save or secret_to_save == "***") and existing_app:
        secret_to_save = existing_app.get("client_secret", "")
        
    if not secret_to_save:
        raise HTTPException(status_code=400, detail="Client Secret 不能为空")

    data["azure_apps"][azure_name] = {
        "tenant_id": config.tenant_id,
        "client_id": config.client_id,
        "client_secret": secret_to_save
    }
    AzureConfigManager.save_config(data)
    return {"status": "success", "message": f"Azure app '{azure_name}' saved."}

@router.get("/apps")
def list_azure_apps():
    """List configured Azure App names. Does not return secrets."""
    data = AzureConfigManager.load_config()
    apps = data.get("azure_apps", {})
    # Return names and tenant_ids only
    safe_apps = {
        name: {
            "tenant_id": app.get("tenant_id"),
            "client_id": app.get("client_id", "")
        } 
        for name, app in apps.items()
    }
    return {"azure_apps": safe_apps}

@router.post("/mapping/{session_name}")
def set_session_mapping(session_name: str, mapping: SessionMapping):
    """Bind a session to an azure_name and upn."""
    data = AzureConfigManager.load_config()
    if "session_mappings" not in data:
        data["session_mappings"] = {}
        
    # Verify azure_name exists
    if mapping.azure_name not in data.get("azure_apps", {}):
        raise HTTPException(status_code=400, detail=f"Azure app '{mapping.azure_name}' not found.")
        
    data["session_mappings"][session_name] = {
        "azure_name": mapping.azure_name,
        "upn": mapping.upn
    }
    AzureConfigManager.save_config(data)
    return {"status": "success", "message": f"Session '{session_name}' mapped."}

@router.get("/mappings")
def list_session_mappings():
    """List all session mappings."""
    data = AzureConfigManager.load_config()
    return {"session_mappings": data.get("session_mappings", {})}

@router.delete("/app/{azure_name}")
def delete_azure_app(azure_name: str):
    """Delete an Azure App and remove all its session mappings."""
    data = AzureConfigManager.load_config()
    apps = data.get("azure_apps", {})
    if azure_name not in apps:
        raise HTTPException(status_code=404, detail=f"Azure app '{azure_name}' not found.")
        
    del apps[azure_name]
    
    # Also unbind any session mapped to this azure_name (reverting to unbound state)
    mappings = data.get("session_mappings", {})
    to_remove = [sess for sess, m in mappings.items() if m.get("azure_name") == azure_name]
    for sess in to_remove:
        del mappings[sess]
        
    AzureConfigManager.save_config(data)
    return {"status": "success", "message": f"Azure app '{azure_name}' and its session mappings removed."}

@router.delete("/mapping/{session_name}")
def delete_session_mapping(session_name: str):
    """Remove a session mapping, reverting the session to an unbound state."""
    data = AzureConfigManager.load_config()
    mappings = data.get("session_mappings", {})
    if session_name not in mappings:
        raise HTTPException(status_code=404, detail=f"Session mapping for '{session_name}' not found.")
        
    del mappings[session_name]
    AzureConfigManager.save_config(data)
    return {"status": "success", "message": f"Session mapping for '{session_name}' removed."}

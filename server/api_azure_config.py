from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from copilot.azure_uploader import AzureConfigManager

router = APIRouter(prefix="/api/azure_config", tags=["azure_config"])

class AzureAppConfig(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: str

class SessionMapping(BaseModel):
    azure_name: str
    upn: str

@router.post("/app/{azure_name}")
def set_azure_app(azure_name: str, config: AzureAppConfig):
    """Create or update an Azure App configuration. Write-only for secrets."""
    data = AzureConfigManager.load_config()
    if "azure_apps" not in data:
        data["azure_apps"] = {}
    
    data["azure_apps"][azure_name] = {
        "tenant_id": config.tenant_id,
        "client_id": config.client_id,
        "client_secret": config.client_secret
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
        name: {"tenant_id": app.get("tenant_id")} 
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Feb 20 17:42:41 2026

@author: rublev.an
"""
"""
MCP Server for Bidzaar Connector API (stdio)
Implements Bidzaar API v5.3
"""
import os
import json
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
import requests
import base64   
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types
import sys

# ============================================================================
# CONFIGURATION
# ============================================================================


class Settings_env(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="/path/to/mcp/bidzaar/.env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    bidzaar_client_id: str
    bidzaar_base_url: str
    bidzaar_client_secret: str
    bidzaar_api_version: str
    bidzaar_user_email: str
    bidzaar_files_base_path: str
    
settings_env = Settings_env()


logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("bidzaar-mcp-server")


# ============================================================================
# BIDZAAR API CLIENT
# ============================================================================

@dataclass
class BidzaarConfig:
    base_url: str = settings_env.bidzaar_base_url
    client_id: str = settings_env.bidzaar_client_id
    client_secret: str = settings_env.bidzaar_client_secret
    user_email: str = settings_env.bidzaar_user_email
    api_version: str = settings_env.bidzaar_api_version
    bidzaar_files_base_path: str = settings_env.bidzaar_files_base_path

    token_expiry_buffer: int = 60


class BidzaarClient:
    """Bidzaar API client with automatic token management"""
    
    def __init__(self, config: BidzaarConfig):
        self.config = config
        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        self.session = requests.Session()
    
    def _get_auth_url(self) -> str:
        return f"{self.config.base_url}/auth/connect/token"
    
    
    def _get_api_url(self, endpoint: str) -> str:
        return f"{self.config.base_url}/api/connector/v{self.config.api_version}/{endpoint.lstrip('/')}"
    
    def _is_token_valid(self) -> bool:
        if not self.access_token or not self.token_expires_at:
            return False
        return datetime.now(timezone.utc) < (self.token_expires_at - timedelta(seconds=self.config.token_expiry_buffer))
    
    def _refresh_token(self) -> None:
        logger.info("Refreshing access token...")
        payload = {
            'grant_type': 'client_credentials',
            'client_id': self.config.client_id,
            'client_secret': self.config.client_secret
        }
        
        response = self.session.post(self._get_auth_url(), data=payload, timeout=30)
        response.raise_for_status()
        token_data = response.json()
        
        self.access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 600)
        self.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        logger.info("Token refreshed successfully")
    
    def _ensure_token(self) -> None:
        if not self._is_token_valid():
            self._refresh_token()
    
    def request(self, method: str, endpoint: str, params: Optional[Dict] = None, 
                json_data: Optional[Dict] = None, **kwargs) -> Any:
        self._ensure_token()
        
        url = self._get_api_url(endpoint)
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'X-Bidzaar-Connector-User-Email': self.config.user_email,
            'Content-Type': 'application/json'
        }
        
        response = self.session.request(
            method=method,
            url=url,
            params=params,
            json=json_data,
            headers=headers,
            timeout=kwargs.get('timeout', 60)
        )
        
        if response.status_code == 401:
            logger.warning("Token expired, refreshing...")
            self._refresh_token()
            headers['Authorization'] = f'Bearer {self.access_token}'
            response = self.session.request(
                method=method, url=url, params=params, 
                json=json_data, headers=headers, timeout=60
            )
        
        response.raise_for_status()
        
        if response.content and 'application/json' in response.headers.get('Content-Type', ''):
            return response.json()
        return response.text if response.content else None


# ============================================================================
# MCP SERVER
# ============================================================================

app = Server("bidzaar-mcp-server")
client = BidzaarClient(BidzaarConfig())

# ============================================================================
# TOOL DEFINITIONS
# ============================================================================

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """List all available Bidzaar API tools"""
    return [

        types.Tool(
            name="create_procedure",
            description="Create a new procurement procedure on Bidzaar platform. Supports draft creation or immediate publishing.",
            inputSchema={
                "type": "object",
                "properties": {
                    # ========== ОСНОВНЫЕ ПОЛЯ ==========
                    "name": {
                        "type": "string",
                        "description": "Procedure name, max 600 chars"
                    },
                    "type": {
                        "type": "integer",
                        "description": "Procedure type: 1=procurement(rfp, rfq, pco) or market monitoring (rfi), 2=sell owned goods, 3=regestry of supplyers",
                        "default": 1
                    },
                    "trading_type": {
                        "type": "integer",
                        "description": "Trading type: 1=fixed_volume (rfp), 2=per_unit (rfq), 4=PCO (qualification), 8=market_monitoring (rfi), 16=registry",
                        "default": 8
                    },
                    "description": {
                        "type": "string",
                        "description": "HTML description, max 4088 chars"
                    },
                    "open_type": {
                        "type": "integer",
                        "description": "0=open (all suppliers), 1=closed (invited only)",
                        "default": 0
                    },
                    "currency": {
                        "type": "string",
                        "description": "Currency: RUB, USD, EUR, etc",
                        "default": "RUB"
                    },
                    "contacts": {
                        "type": "string",
                        "description": "Contact information: names, phones, emails"
                    },
                    
                    # ========== ДАТЫ ==========
                    "acceptance_end_date": {
                        "type": "string",
                        "description": "ISO 8601 end date for proposal submission (YYYY-MM-DDTHH:MM:SSZ)"
                    },
                    "acceptance_end_days": {
                        "type": "integer",
                        "description": "Number of days for proposal submission (alternative to acceptance_end_date)",
                        "default": 7
                    },
                    "approximate_deadline_for_summing_up": {
                        "type": "integer",
                        "description": "Days for summing up results",
                        "default": 5
                    },
                    "submission_start_date": {
                        "type": "string",
                        "description": "ISO 8601 start date for proposal submission"
                    },
                    
                    # ========== ПОЗИЦИИ ==========
                    "positions_enabled": {
                        "type": "boolean",
                        "description": "Enable position specification",
                        "default": True
                    },
                    "positions": {
                        "type": "array",
                        "description": "Array of positions",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Position name"},
                                "count": {"type": "number", "description": "Quantity", "default": 1},
                                "unit": {"type": "string", "description": "Unit of measurement", "default": "шт."},
                                "price": {"type": "number", "description": "Price per unit", "default": 0},
                                "description": {"type": "string", "description": "Position description"},
                                "bet_price": {"type": "number", "description": "Bid price for position", "default": 0},
                                "files": {
                                    "type": "array",
                                    "description": "Files for this position",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string", "description": "Uploaded file id"},
                                            "name": {"type": "string", "description": "File name"},
                                            "extension": {"type": "string", "description": "File extension"},
                                            "length": {"type": "integer", "description": "File size in bytes"}
                                        }
                                    }
                                },
                                "additional_fields_values": {
                                    "type": "array",
                                    "description": "Additional field values",
                                    "items": {"type": "object"}
                                }
                            }
                        }
                    },
                    
                    # ========== ФАЙЛЫ ==========
                    "common_files": {
                        "type": "array",
                        "description": "Common files for the procedure (technical specifications, terms, etc.)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Uploaded file id from /files/upload"},
                                "name": {"type": "string", "description": "File name"},
                                "extension": {"type": "string", "description": "File extension"},
                                "length": {"type": "integer", "description": "File size in bytes"}
                            },
                            "required": ["id", "name", "extension","length"]
                        }
                    },
                    
                    # ========== ТЕГИ ==========
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for the procedure"
                    },
                    
                    # ========== НАСТРОЙКИ ТОРГОВ ==========
                    "bet_up_down": {
                        "type": "boolean",
                        "description": "Can participants both increase and decrease price",
                        "default": True
                    },
                    "bet_step": {
                        "type": "number",
                        "description": "Minimum bid step (for trading_type=1,2,4)",
                        "default": 0.01
                    },
                    "bet_step_type": {
                        "type": "integer",
                        "description": "0=percentage, 1=currency",
                        "default": 1
                    },
                    "bet_reference": {
                        "type": "integer",
                        "description": "0=from own proposal, 1=from best proposal",
                        "default": 0
                    },
                    "bet_price": {
                        "type": "number",
                        "description": "Expected/max/min bid price",
                        "default": 0
                    },
                    
                    # ========== УЧАСТНИКИ И ВИДИМОСТЬ ==========
                    "other_participants_visibility": {
                        "type": "integer",
                        "description": "0=own only, 1=own rank, 2=competitors without names, 3=competitors with names, 4=best prices, 5=rank and best prices",
                        "default": 0
                    },
                    "owner_visibility": {
                        "type": "integer",
                        "description": "0=organizer sees proposals, 1=organizer does not see proposals",
                        "default": 0
                    },
                    "vat_enabled": {
                        "type": "boolean",
                        "description": "Consider VAT when selecting winner: false=without VAT, true=with VAT",
                        "default": False
                    },
                    "alternative_proposals": {
                        "type": "integer",
                        "description": "Number of alternative proposals allowed (max 10, 0=disabled)",
                        "default": 0
                    },
                    
                    # ========== АВТОПРОДЛЕНИЕ И УВЕДОМЛЕНИЯ ==========
                    "prolongation_time": {
                        "type": "number",
                        "description": "Auto-prolongation in minutes (max 99, 0=disabled)",
                        "default": 0
                    },
                    "acceptance_end_notification_hours": {
                        "type": "integer",
                        "description": "Hours before end to notify participants (max 99, 0=disabled)",
                        "default": 0
                    },
                    "additional_acceptance_end_notification_hours": {
                        "type": "integer",
                        "description": "Additional notification hours before end",
                        "default": 0
                    },
                    
                    # ========== NDA ==========
                    "nda_enabled": {
                        "type": "boolean",
                        "description": "Enable NDA/pre-qualification stage",
                        "default": False
                    },
                    "nda_description": {
                        "type": "string",
                        "description": "NDA description (HTML, required if nda_enabled=True)"
                    },
                    "nda_files": {
                        "type": "array",
                        "description": "NDA documents",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "extension": {"type": "string"},
                                "length": {"type": "integer"}
                            }
                        }
                    },
                    
                    # ========== АНКЕТА УЧАСТНИКА ==========
                    "participant_questionnaire_enabled": {
                        "type": "boolean",
                        "description": "Enable participant questionnaire",
                        "default": False
                    },
                    "participant_questionnaire": {
                        "type": "array",
                        "description": "Participant questionnaire sections",
                        "items": {"type": "object"}
                    },
                    "participant_application_files": {
                        "type": "boolean",
                        "description": "Allow document attachments to application",
                        "default": False
                    },
                    
                    # ========== НЕЦЕНОВЫЕ КРИТЕРИИ ==========
                    "questionnaire_enabled": {
                        "type": "boolean",
                        "description": "Enable non-price criteria questionnaire",
                        "default": False
                    },
                    "questionnaire": {
                        "type": "array",
                        "description": "Non-price criteria questionnaire",
                        "items": {"type": "object"}
                    },
                    
                    # ========== РАНЖИРОВАНИЕ ==========
                    "proposal_rank_method": {
                        "type": "integer",
                        "description": "0=by price and time, 1=by price, 2=custom method",
                        "default": 0
                    },
                    "proposal_rank_order": {
                        "type": "integer",
                        "description": "0=highest score first, 1=lowest score first",
                        "default": 0
                    },
                    "proposal_rank_email": {
                        "type": "string",
                        "description": "Email for ranking error notifications"
                    },
                    "proposal_rank_notification_enabled": {
                        "type": "boolean",
                        "description": "Enable ranking error notifications",
                        "default": True
                    },
                    "proposal_rank_file": {
                        "type": "object",
                        "description": "Custom ranking method file",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "extension": {"type": "string"},
                            "length": {"type": "integer"}
                        }
                    },
                    
                    # ========== ДОПОЛНИТЕЛЬНЫЕ ВАЛЮТЫ ==========
                    "additional_currencies": {
                        "type": "array",
                        "description": "Additional currencies",
                        "items": {
                            "type": "object",
                            "properties": {
                                "currency": {"type": "string"},
                                "amount": {"type": "number"},
                                "rate": {"type": "number"}
                            }
                        }
                    },
                    
                    # ========== АДРЕСА ДОСТАВКИ ==========
                    "delivery_addresses": {
                        "type": "array",
                        "description": "Delivery addresses",
                        "items": {
                            "type": "object",
                            "properties": {
                                "country": {"type": "string"},
                                "region": {"type": "string"},
                                "area": {"type": "string"},
                                "cityType": {"type": "string"},
                                "city": {"type": "string"},
                                "building": {"type": "string"},
                                "addressComment": {"type": "string"}
                            }
                        }
                    },
                    
                    # ========== СВЯЗАННЫЕ ПРОЦЕДУРЫ ==========
                    "linked_procedures": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Linked procedure UUIDs"
                    },
                    
                    # ========== ДРУГИЕ НАСТРОЙКИ ==========
                    "participant_documents_acceptance_period": {
                        "type": "integer",
                        "description": "Hours for document submission after proposal acceptance"
                    },
                    "comment": {
                        "type": "string",
                        "description": "Internal procedure comment (max 1024 chars)"
                    },
                    "budget": {
                        "type": "number",
                        "description": "Procedure budget (for per_unit trading type)"
                    },
                    "identifier": {
                        "type": "string",
                        "description": "External identifier (max 32 chars)"
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Category UUIDs"
                    },
                    "segment_id": {
                        "type": "string",
                        "description": "Business segment UUID"
                    },
                    "emoji": {
                        "type": "string",
                        "description": "Emoji icon (1 char)"
                    },
                    
                    # ========== ПУБЛИКАЦИЯ ==========
                    "publish_immediately": {
                        "type": "boolean",
                        "description": "Publish immediately or create draft",
                        "default": True
                    }
                },
                "required": ["name"]
            }
        ),
        types.Tool(
            name="get_procedure",
            description="Get detailed information about a specific procedure by its UUID. Returns complete procedure data including status, positions, participants count, etc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="update_procedure",
            description="Update existing procedure parameters. Can modify name, description, end date, positions, tags, contacts, and other settings. For published procedures, changes will cause republication except for 'contacts', 'tags', 'additionalCurrencies'. Use custom_mail to notify participants. Note: type and tradingType cannot be changed after creation.",
            inputSchema={
                "type": "object",
                "properties": {
                    # ========== ОСНОВНЫЕ ПОЛЯ ==========
                    "procedure_id": {
                        "type": "string",
                        "description": "UUID of the procedure to update (required)"
                    },
                    "name": {
                        "type": "string",
                        "description": "New procedure name, max 600 chars"
                    },
                    "description": {
                        "type": "string",
                        "description": "New HTML description, max 4088 chars. Empty string to delete."
                    },
                    "acceptance_end_date": {
                        "type": "string",
                        "description": "New end date for proposal submission (ISO 8601: YYYY-MM-DDTHH:mm:ss.sssZ)"
                    },
                    "open_type": {
                        "type": "integer",
                        "description": "0=open (all suppliers), 1=closed (invited only)"
                    },
                    "currency": {
                        "type": "string",
                        "description": "Currency: RUB, USD, EUR, etc"
                    },
                    "contacts": {
                        "type": "string",
                        "description": "Contact information (max 2048 chars). Null to delete. Changes do NOT cause republication."
                    },
                    "emoji": {
                        "type": "string",
                        "description": "Emoji icon (1 char)"
                    },
                    
                    # ========== ДАТЫ ==========
                    "approximate_deadline_for_summing_up": {
                        "type": "number",
                        "description": "Days for summing up results (min 1, max 45)"
                    },
                    "submission_start_date": {
                        "type": "string",
                        "description": "ISO 8601 start date for proposal submission"
                    },
                    
                    # ========== ФАЙЛЫ ==========
                    "common_files": {
                        "type": "array",
                        "description": "Common files for the procedure. Empty array to delete.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Uploaded file id"},
                                "name": {"type": "string", "description": "File name"},
                                "extension": {"type": "string", "description": "File extension"},
                                "length": {"type": "integer", "description": "File size in bytes"}
                            }
                        }
                    },
                    
                    # ========== ТЕГИ ==========
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for the procedure (max 50 chars each). Empty array to delete. Changes do NOT cause republication."
                    },
                    
                    # ========== ПОЗИЦИИ ==========
                    "positions_enabled": {
                        "type": "boolean",
                        "description": "Enable position specification. WARNING: Changing this is a major change!"
                    },
                    "position_groups": {
                        "type": "array",
                        "description": "Position groups. Complete replacement of existing groups.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "origin_id": {"type": "string", "description": "Unique identifier for the group (UUID)"},
                                "name": {"type": "string", "description": "Group name"},
                                "deviation_type": {"type": "integer", "description": "0=all positions required, 1=with deviation, 2=partial submission"},
                                "bet_up_down": {"type": "boolean", "description": "Can both increase and decrease price"},
                                "bet_step": {"type": "number", "description": "Minimum bid step"},
                                "bet_step_type": {"type": "integer", "description": "0=percentage, 1=currency"},
                                "bet_reference": {"type": "integer", "description": "0=from own, 1=from best"},
                                "bet_price": {"type": "number", "description": "Expected/max/min bid price"},
                                "participant_files": {"type": "boolean", "description": "Allow participants to attach files"},
                                "additional_fields": {"type": "array", "description": "Additional fields for positions"},
                                "positions": {
                                    "type": "array",
                                    "description": "Positions in this group",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "origin_id": {"type": "string", "description": "Unique identifier for position (UUID)"},
                                            "name": {"type": "string", "description": "Position name"},
                                            "description": {"type": "string", "description": "Position description"},
                                            "count": {"type": "number", "description": "Quantity"},
                                            "unit": {"type": "string", "description": "Unit of measurement"},
                                            "price": {"type": "number", "description": "Price per unit"},
                                            "bet_price": {"type": "number", "description": "Bid price"},
                                            "additional_fields_values": {"type": "array", "description": "Additional field values"},
                                            "files": {
                                                "type": "array",
                                                "description": "Files for this position",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "id": {"type": "string"},
                                                        "name": {"type": "string"},
                                                        "extension": {"type": "string"},
                                                        "length": {"type": "integer"}
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    },
                    
                    # ========== НАСТРОЙКИ ТОРГОВ (без позиций) ==========
                    "bet_up_down": {
                        "type": "boolean",
                        "description": "Can participants both increase and decrease price (when positions_enabled=false)"
                    },
                    "bet_step": {
                        "type": "number",
                        "description": "Minimum bid step (when positions_enabled=false)"
                    },
                    "bet_step_type": {
                        "type": "integer",
                        "description": "0=percentage, 1=currency (when positions_enabled=false)"
                    },
                    "bet_reference": {
                        "type": "integer",
                        "description": "0=from own proposal, 1=from best proposal (when positions_enabled=false)"
                    },
                    "bet_price": {
                        "type": "number",
                        "description": "Expected/max/min bid price (when positions_enabled=false)"
                    },
                    
                    # ========== УЧАСТНИКИ И ВИДИМОСТЬ ==========
                    "other_participants_visibility": {
                        "type": "integer",
                        "description": "0=own only, 1=own rank, 2=competitors without names, 3=competitors with names, 4=best prices, 5=rank and best prices"
                    },
                    "owner_visibility": {
                        "type": "integer",
                        "description": "0=organizer sees proposals, 1=organizer does not see proposals"
                    },
                    "vat_enabled": {
                        "type": "boolean",
                        "description": "Consider VAT when selecting winner: false=without VAT, true=with VAT"
                    },
                    "alternative_proposals": {
                        "type": "integer",
                        "description": "Number of alternative proposals allowed (max 10, 0=disabled)"
                    },
                    
                    # ========== АВТОПРОДЛЕНИЕ И УВЕДОМЛЕНИЯ ==========
                    "prolongation_time": {
                        "type": "number",
                        "description": "Auto-prolongation in minutes (max 99, 0=disabled)"
                    },
                    "acceptance_end_notification_hours": {
                        "type": "integer",
                        "description": "Hours before end to notify participants (max 99, 0=disabled)"
                    },
                    "additional_acceptance_end_notification_hours": {
                        "type": "integer",
                        "description": "Additional notification hours before end"
                    },
                    
                    # ========== ДОПОЛНИТЕЛЬНЫЕ ВАЛЮТЫ ==========
                    "additional_currencies": {
                        "type": "array",
                        "description": "Additional currencies. Cannot remove or change currency after publication. Amount and rate can be changed.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "currency": {"type": "string", "description": "Currency code (cannot change after publication)"},
                                "amount": {"type": "number", "description": "Amount in additional currency"},
                                "rate": {"type": "number", "description": "Exchange rate to base currency"}
                            }
                        }
                    },
                    
                    # ========== NDA ==========
                    "nda_enabled": {
                        "type": "boolean",
                        "description": "Enable NDA/pre-qualification stage"
                    },
                    "nda_description": {
                        "type": "string",
                        "description": "NDA description (HTML, required if nda_enabled=True)"
                    },
                    "nda_files": {
                        "type": "array",
                        "description": "NDA documents",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "extension": {"type": "string"},
                                "length": {"type": "integer"}
                            }
                        }
                    },
                    
                    # ========== АНКЕТА УЧАСТНИКА ==========
                    "participant_questionnaire_enabled": {
                        "type": "boolean",
                        "description": "Enable participant questionnaire"
                    },
                    "participant_questionnaire": {
                        "type": "array",
                        "description": "Participant questionnaire sections",
                        "items": {"type": "object"}
                    },
                    "participant_application_files": {
                        "type": "boolean",
                        "description": "Allow document attachments to application"
                    },
                    "participant_documents_acceptance_period": {
                        "type": "integer",
                        "description": "Hours for document submission after proposal acceptance"
                    },
                    
                    # ========== НЕЦЕНОВЫЕ КРИТЕРИИ ==========
                    "questionnaire_enabled": {
                        "type": "boolean",
                        "description": "Enable non-price criteria questionnaire"
                    },
                    "questionnaire": {
                        "type": "array",
                        "description": "Non-price criteria questionnaire",
                        "items": {"type": "object"}
                    },
                    
                    # ========== РАНЖИРОВАНИЕ ==========
                    "proposal_rank_method": {
                        "type": "integer",
                        "description": "0=by price and time, 1=by price, 2=custom method"
                    },
                    "proposal_rank_order": {
                        "type": "integer",
                        "description": "0=highest score first, 1=lowest score first"
                    },
                    "proposal_rank_email": {
                        "type": "string",
                        "description": "Email for ranking error notifications"
                    },
                    "proposal_rank_notification_enabled": {
                        "type": "boolean",
                        "description": "Enable ranking error notifications"
                    },
                    "proposal_rank_file": {
                        "type": "object",
                        "description": "Custom ranking method file",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "extension": {"type": "string"},
                            "length": {"type": "integer"}
                        }
                    },
                    
                    # ========== АДРЕСА ДОСТАВКИ ==========
                    "delivery_addresses": {
                        "type": "array",
                        "description": "Delivery addresses. Empty array to delete.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "country": {"type": "string"},
                                "region": {"type": "string"},
                                "area": {"type": "string"},
                                "city_type": {"type": "string"},
                                "city": {"type": "string"},
                                "building": {"type": "string"},
                                "address_comment": {"type": "string"}
                            }
                        }
                    },
                    
                    # ========== СВЯЗАННЫЕ ПРОЦЕДУРЫ ==========
                    "linked_procedures": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Linked procedure UUIDs. Empty array to delete."
                    },
                    
                    # ========== ДРУГИЕ НАСТРОЙКИ ==========
                    "comment": {
                        "type": "string",
                        "description": "Internal procedure comment (max 1024 chars). Empty string to delete."
                    },
                    "budget": {
                        "type": "number",
                        "description": "Procedure budget (for per_unit trading type)"
                    },
                    "identifier": {
                        "type": "string",
                        "description": "External identifier (max 32 chars)"
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Category UUIDs. Empty array to delete."
                    },
                    "segment_id": {
                        "type": "string",
                        "description": "Business segment UUID"
                    },
                    "culture_name": {
                        "type": "string",
                        "description": "Deprecated: Language for email notifications: 'ru' or 'en'",
                        "default": "ru"
                    },
                    
                    # ========== ПАРАМЕТРЫ ЗАПРОСА ==========
                    "custom_mail": {
                        "type": "string",
                        "description": "Message to send to all participants by email and chat (max 1024 chars). Used as query parameter, not in body."
                    },
                    "rollback_proposals": {
                        "type": "boolean",
                        "description": "Whether to reject existing proposals if changes are major. Used as query parameter, not in body.",
                        "default": False
                    }
                },
                "required": ["procedure_id"]
            }
        ),      
        types.Tool(
            name="delete_procedure_draft",
            description="Delete a draft procedure. Only works for procedures in draft status (not published).",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the draft procedure to delete"}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="publish_procedure",
            description="Publish a draft procedure. After publishing, suppliers can submit proposals. Can set publish_date for delayed publication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure to publish"},
                    "publish_date": {"type": "string", "description": "Optional ISO 8601 date for delayed publication"}
                },
                "required": ["procedure_id"]
            }
        ),
        
        
        # ========== PARTICIPANT MANAGEMENT ==========
        types.Tool(
            name="get_participants",
            description="Get list of all participants in a procedure with their status, contact info, and company details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="invite_participants",
            description="Invite participants to a procedure by TIN/KPP or email. Can invite registered companies by TIN, or send invitations to email addresses.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "invitations": {"type": "array", "items": {"type": "object"}, "description": "List of invitations with tin, email, or companyName"}
                },
                "required": ["procedure_id", "invitations"]
            }
        ),
        types.Tool(
            name="block_participants",
            description="Block participants from submitting proposals.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "participant_ids": {"type": "array", "items": {"type": "string"}, "description": "List of participant UUIDs or names to block."},
                    "block_reason": {"type": "string", "description": "Reason for blocking (visible to participant)"}
                },
                "required": ["procedure_id", "participant_ids"]
            }
        ),
        types.Tool(
            name="unblock_participants",
            description="Unblock previously blocked participants.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "participant_ids": {"type": "array", "items": {"type": "string"}, "description": "List of participant UUIDs or names to unblock"}
                },
                "required": ["procedure_id", "participant_ids"]
            }
        ),
 
        types.Tool(
            name="get_participants_with_details",
            description="Get list of all participants in a procedure with search capability. Can filter by name, INN, or email.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "search": {"type": "string", "description": "Optional search string (name, INN, or email)"}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="get_blocked_participants",
            description="Get list of participants blocked in any stage of the procedure.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
                
        # ========== EVENTS ==========
        types.Tool(
            name="get_events",
            description="Get company events filtered by date, type, procedure, or stage. Returns chronological list of events with details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date ISO 8601"},
                    "event_types": {"type": "array", "items": {"type": "integer"}, "description": "Event type codes: 1=published, 2=changed, 4=acceptance_ended, 7=completed, 11=proposal_submitted, etc"},
                    "procedure_id": {"type": "string", "description": "Filter by procedure UUID"},
                    "stage_id": {"type": "string", "description": "Filter by stage UUID"}
                }
            }
        ),
        
 
        types.Tool(
            name="complete_without_winners",
            description="Complete procedure without selecting winners. Provide reason and optional message to participants.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "reason": {"type": "string", "description": "Reason for completion without winners"},
                    "message": {"type": "string", "description": "Message to all participants"}
                },
                "required": ["procedure_id"]
            }
        ),
 
        
        # ========== INFORMATION TOOLS ==========
        types.Tool(
            name="get_companies_info",
            description="Get company information by UUIDs. Returns company details including name, TIN, registration date, contacts, and location.",
            inputSchema={
                "type": "object",
                "properties": {
                    "company_ids": {"type": "array", "items": {"type": "string"}, "description": "List of company UUIDs (max 30)"}
                },
                "required": ["company_ids"]
            }
        ),
  
        types.Tool(
            name="get_tags",
            description="Get tags from the platform with filtering. Returns tags matching search criteria with minimum usage count.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search_criteria": {"type": "string", "description": "Search string for tags"},
                    "usage_count": {"type": "integer", "description": "Minimum usage count", "default": 10},
                    "page": {"type": "integer", "description": "Page number (0-based)", "default": 0},
                    "size": {"type": "integer", "description": "Page size", "default": 40}
                }
            }
        ),
    
        
        # ========== FILE MANAGEMENT ==========
        types.Tool(
            name="upload_files",
            description="Upload files to company storage",
            inputSchema={
                "type": "object",
                "properties": {
                    "files": {"type": "array", "items": {"type": "object"}, "description": "List of files with name, extension, and base64 content or full path to file"}
                },
                "required": ["files"]
            }
        ),
        types.Tool(
            name="get_file",
            description="Get a file from platform storage by file ID. Returns base64 encoded content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "UUID of the file"}
                },
                "required": ["file_id"]
            }
        ),
        
        # ========== REPORTS ==========
        types.Tool(
            name="get_comparison_file",
            description="Request generation of comparison report (competitor analysis) in XLSX format. Returns task ID for later retrieval.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "sort_type": {"type": "integer", "description": "Sort proposals: 0=by rank, 1=by selection, 2=by completeness, 3=by update", "default": 0}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="get_report_file",
            description="Download generated report file by task ID. Returns file content as base64.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "report_id": {"type": "string", "description": "Task ID from report generation request"}
                },
                "required": ["procedure_id", "report_id"]
            }
        ),
        
        # ========== APPLICATION MANAGEMENT ==========
        types.Tool(
            name="get_participant_applications",
            description="Get all applications submitted by a participant in a procedure. Returns application status, dates, and attached files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "participant_id": {"type": "string", "description": "UUID of the participant"}
                },
                "required": ["procedure_id", "participant_id"]
            }
        ),
        types.Tool(
            name="request_documents",
            description="Request additional documents from participants. Available during proposal submission, evaluation, and completed statuses.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "participant_ids": {"type": "array", "items": {"type": "string"}, "description": "Participant UUIDs to request documents from"},
                    "end_date": {"type": "string", "description": "Deadline for document submission ISO 8601"},
                    "comment": {"type": "string", "description": "Message to participants"}
                },
                "required": ["procedure_id", "participant_ids", "end_date"]
            }
        ),
        
    
        
        # ========== AI IMPROVEMENT ==========
        types.Tool(
            name="improve_description",
            description="Generate improved procedure description using AI. Returns improved text and suggests missing sections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "description": {"type": "string", "description": "Original description to improve"}
                },
                "required": ["procedure_id", "description"]
            }
        ),
        types.Tool(
            name="predict_and_apply_tags",
            description="Generate tags for specific procedure by its UUID. Returns complete procedure data including status, positions, participants count, etc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        # ========== RETURN TO EVALUATION ==========
        types.Tool(
            name="return_to_evaluation",
            description="Return completed procedure back to evaluation status. Allows reopening completed procedures.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "reason": {"type": "string", "description": "Reason for returning"},
                    "subject": {"type": "string", "description": "Subject"},
                    "message": {"type": "string", "description": "Message to participants"}
                },
                "required": ["procedure_id"]
            }
        ),
        
    ]


# ============================================================================
# TOOL HANDLERS
# ============================================================================

async def execute_tool(tool_name: str, arguments: Dict) -> Any:
    """Execute tool with given arguments"""
    logger.info(f"🔧 Executing tool: {tool_name}")
    logger.debug(f"Arguments: {json.dumps(arguments, ensure_ascii=False, default=str)}")
    try:
        if tool_name == "create_procedure":
            return await create_procedure_handler(arguments)
        elif tool_name == "get_procedure":
            return await get_procedure_handler(arguments)
        elif tool_name == "update_procedure":
            return await update_procedure_handler(arguments)
        elif tool_name == "delete_procedure_draft":
            return await delete_procedure_draft_handler(arguments)
        elif tool_name == "publish_procedure":
            return await publish_procedure_handler(arguments)
        elif tool_name == "get_proposals_ids":
            return await get_proposals_ids_handler(arguments)
        elif tool_name == "get_proposals":
            return await get_proposals_handler(arguments)
        elif tool_name == "get_proposals_ranks":
            return await get_proposals_ranks_handler(arguments)
        elif tool_name == "rollback_proposal":
            return await rollback_proposal_handler(arguments)
        elif tool_name == "get_participants":
            return await get_participants_handler(arguments)
        elif tool_name == "get_participants_with_details":
            return await get_participants_with_details_handler(arguments)
        elif tool_name == "invite_participants":
            return await invite_participants_handler(arguments)
        elif tool_name == "block_participants":
            return await block_participants_handler(arguments)
        elif tool_name == "unblock_participants":
            return await unblock_participants_handler(arguments)
        elif tool_name == "approve_participants":
            return await approve_participants_handler(arguments)
        elif tool_name == "reject_participants":
            return await reject_participants_handler(arguments)
        elif tool_name == "get_blocked_participants":
            return await get_blocked_participants_handler(arguments)
        elif tool_name == "get_events":
            return await get_events_handler(arguments)
        elif tool_name == "complete_with_winners":
            return await complete_with_winners_handler(arguments)
        elif tool_name == "complete_without_winners":
            return await complete_without_winners_handler(arguments)
        elif tool_name == "finish_proposals_acceptance":
            return await finish_proposals_acceptance_handler(arguments)
        elif tool_name == "get_stages":
            return await get_stages_handler(arguments)
        elif tool_name == "get_stages_full_info":
            return await get_stages_full_info_handler(arguments)
        elif tool_name == "announce_new_stage":
            return await announce_new_stage_handler(arguments)
        elif tool_name == "get_choices":
            return await get_choices_handler(arguments)
        elif tool_name == "set_winners":
            return await set_winners_handler(arguments)
        elif tool_name == "get_companies_info":
            return await get_companies_info_handler(arguments)
        elif tool_name == "get_segments":
            return await get_segments_handler(arguments)
        elif tool_name == "get_tags":
            return await get_tags_handler(arguments)
        elif tool_name == "get_special_conditions":
            return await get_special_conditions_handler(arguments)
        elif tool_name == "get_chat_spaces":
            return await get_chat_spaces_handler(arguments)
        elif tool_name == "get_chats":
            return await get_chats_handler(arguments)
        elif tool_name == "send_chat_message":
            return await send_chat_message_handler(arguments)
        elif tool_name == "upload_files":
            return await upload_files_handler(arguments)
        elif tool_name == "get_file":
            return await get_file_handler(arguments)
        elif tool_name == "get_comparison_file":
            return await get_comparison_file_handler(arguments)
        elif tool_name == "get_report_file":
            return await get_report_file_handler(arguments)
        elif tool_name == "get_participant_applications":
            return await get_participant_applications_handler(arguments)
        elif tool_name == "request_documents":
            return await request_documents_handler(arguments)
        elif tool_name == "apply_promo_code":
            return await apply_promo_code_handler(arguments)
        elif tool_name == "add_additional_currency":
            return await add_additional_currency_handler(arguments)
        elif tool_name == "update_additional_currencies":
            return await update_additional_currencies_handler(arguments)
        elif tool_name == "cancel_delayed_publication":
            return await cancel_delayed_publication_handler(arguments)
        elif tool_name == "cancel_stage":
            return await cancel_stage_handler(arguments)
        elif tool_name == "improve_description":
            return await improve_description_handler(arguments)
        elif tool_name == "return_to_evaluation":
            return await return_to_evaluation_handler(arguments)
        elif tool_name == "allow_price_change":
            return await allow_price_change_handler(arguments)
        elif tool_name == "reject_price_change_request":
            return await reject_price_change_request_handler(arguments)
        elif tool_name == "predict_and_apply_tags":
            return await predict_and_apply_handler(arguments)
        else:
            return {"error": f"Unknown tool: {tool_name}"}
            
    except Exception as e:
        logger.exception(f"Error executing {tool_name}")
        return {"error": str(e)}


# ============================================================================
# HANDLER IMPLEMENTATIONS
# ============================================================================
async def create_procedure_handler(args: Dict) -> Dict:
    """Create procedure handler matching the full API schema"""
    logger.info(f"📝 Creating procedure: {args.get('name')}")
    
    try:
        is_draft = not args.get("publish_immediately", True)
        trading_type = args.get("trading_type", 8)
        has_positions = args.get("positions_enabled", True) and bool(args.get("positions"))
        
        # Обработка даты окончания
        acceptance_end_date = args.get("acceptance_end_date")
        if not acceptance_end_date:
            days = max(args.get("acceptance_end_days", 7), 7)
            acceptance_end_date = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        
        # Базовые поля
        data = {
            "name": args["name"],
            "type": args.get("type", 1),
            "tradingType": trading_type,
            "description": args.get("description", ""),
            "openType": args.get("open_type", 0),
            "currency": args.get("currency", "RUB"),
            "acceptanceEndDate": acceptance_end_date,
            "approximateDeadlineForSummingUp": args.get("approximate_deadline_for_summing_up", 5),
            "contacts": args.get("contacts", f"Контактное лицо: {settings_env.bidzaar_user_email}"),
            "users": [
                {
                    "email": settings_env.bidzaar_user_email,
                    "role": 2,
                    "isResponsible": True,
                    "isResponsibleForApplications": True
                }
            ],
            "positionsEnabled": has_positions,
            "otherParticipantsVisibility": args.get("other_participants_visibility", 0),
            "ownerVisibility": args.get("owner_visibility", 0),
            "vatEnabled": args.get("vat_enabled", False),
            "alternativeProposals": args.get("alternative_proposals", 0),
            "prolongationTime": args.get("prolongation_time", 0),
            "acceptanceEndNotificationHours": args.get("acceptance_end_notification_hours", 0),
            "additionalAcceptanceEndNotificationHours": args.get("additional_acceptance_end_notification_hours", 0),
            "ndaEnabled": args.get("nda_enabled", False),
            "questionnaireEnabled": args.get("questionnaire_enabled", False),
            "proposalRankMethod": args.get("proposal_rank_method", 0),
            "proposalRankOrder": args.get("proposal_rank_order", 0),
            "proposalRankNotificationEnabled": args.get("proposal_rank_notification_enabled", True),
            "cultureName": args.get("culture_name", "ru"),
            "tags": args.get("tags", []),
            "commonFiles": args.get("common_files", []),
            "deliveryAddresses": args.get("delivery_addresses", []),
            "linkedProcedures": args.get("linked_procedures", []),
            "additionalCurrencies": args.get("additional_currencies", []),
            "categories": args.get("categories", []),
            "participantQuestionnaireEnabled": args.get("participant_questionnaire_enabled", False),
            "participantApplicationFiles": args.get("participant_application_files", False),
            "participantDocumentsAcceptancePeriod": args.get("participant_documents_acceptance_period"),
            "comment": args.get("comment"),
            "budget": args.get("budget"),
            "identifier": args.get("identifier"),
            "segmentId": args.get("segment_id"),
            "emoji": args.get("emoji"),
            "submissionStartDate": args.get("submission_start_date")
        }
        
        # Удаляем None значения
        data = {k: v for k, v in data.items() if v is not None}
        
        # Добавляем NDA файлы если есть
        if args.get("nda_files"):
            data["ndaFiles"] = args["nda_files"]
        
        if args.get("nda_description"):
            data["ndaDescription"] = args["nda_description"]
        
        # Добавляем анкету если есть
        if args.get("participant_questionnaire"):
            data["participantQuestionnaire"] = args["participant_questionnaire"]
        
        if args.get("questionnaire"):
            data["questionnaire"] = args["questionnaire"]
        
        # Добавляем файл ранжирования если есть
        if args.get("proposal_rank_file"):
            data["proposalRankFile"] = args["proposal_rank_file"]
        
        if args.get("proposal_rank_email"):
            data["proposalRankEmail"] = args["proposal_rank_email"]
        
        # Добавляем позиции
        if has_positions:
            data["positionGroups"] = [{
                "originId": str(uuid.uuid4()),
                "name": "Основная группа",
                "deviationType": args.get("deviation_type", 0),
                "betUpDown": args.get("bet_up_down", True),
                "betStep": args.get("bet_step", 0.01),
                "betStepType": args.get("bet_step_type", 1),
                "betReference": args.get("bet_reference", 0),
                "betPrice": args.get("bet_price", 0),
                "participantFiles": args.get("participant_files", False),
                "additionalFields": args.get("additional_fields", []),
                "positions": []
            }]
            
            for p in args["positions"]:
                position = {
                    "originId": str(uuid.uuid4()),
                    "name": p.get("name", "Товар"),
                    "description": p.get("description", ""),
                    "count": float(p.get("count", 1)),
                    "unit": p.get("unit", "шт."),
                    "price": float(p.get("price", 0)),
                    "betPrice": float(p.get("bet_price", 0)),
                    "additionalFieldsValues": p.get("additional_fields_values", []),
                    "files": p.get("files", [])
                }
                data["positionGroups"][0]["positions"].append(position)
        else:
            # Для торгов без позиций
            if trading_type in [1, 2, 4]:
                data["betUpDown"] = args.get("bet_up_down", True)
                data["betStep"] = args.get("bet_step", 0.01)
                data["betStepType"] = args.get("bet_step_type", 1)
                data["betReference"] = args.get("bet_reference", 0)
                data["betPrice"] = args.get("bet_price", 0)
        
        # Для мониторинга рынка
        if trading_type == 8:
            data["otherParticipantsVisibility"] = 3
            data["betUpDown"] = True
        
        logger.info(f"📤 Sending to API: {json.dumps(data, ensure_ascii=False, default=str)[:2000]}")
        
        endpoint = "procedures/draft" if is_draft else "procedures/create-publish"
        
        
        try:
            result = client.request("POST", endpoint, json_data=data)
            logger.info(f"✅ Procedure created: {result.get('id')}")
            return result
            

        except requests.exceptions.HTTPError as e:
            error_response = {
                "success": False,
                "error": "HTTP Error",
                "status_code": e.response.status_code if e.response else None,
                "message": str(e)
            }
            
            if e.response is not None:
                try:
                    error_body = e.response.json()
                    error_response["api_error"] = error_body
                    
                    if "message" in error_body:
                        error_response["error_message"] = error_body["message"]
                    if "code" in error_body:
                        error_response["error_code"] = error_body["code"]
                    if "details" in error_body:
                        error_response["error_details"] = error_body["details"]
                        
                except json.JSONDecodeError:
                    error_response["response_body"] = e.response.text
            
            logger.error(f"❌ HTTP Error creating procedure: {error_response}")
            return error_response
            
    except Exception as e:
        logger.error(f"❌ Failed to create procedure: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Internal Error",
            "message": str(e),
            "exception_type": type(e).__name__
        }

async def predict_and_apply_handler(args: Dict) -> Dict:
    params = None
    return client.request("POST", f"tags/{args['procedure_id']}/predict-and-apply")


async def get_procedure_handler(args: Dict) -> Dict:
    return client.request("GET", f"procedures/{args['procedure_id']}")



async def update_procedure_handler(args: Dict) -> Dict:
    """Update procedure handler - supports adding positions to RFI"""
    logger.info(f"📝 Updating procedure: {args.get('procedure_id')}")
    
    try:
        procedure_id = args.pop("procedure_id")
        
        current_proc = client.request("GET", f"procedures/{procedure_id}")
        if not current_proc:
            raise Exception(f"Procedure {procedure_id} not found")
        
        logger.info(f"Current procedure tradingType: {current_proc.get('tradingType')}")
        logger.info(f"Current positionsEnabled: {current_proc.get('positionsEnabled')}")
        
        # Извлекаем query параметры
        query_params = {}
        if "custom_mail" in args:
            query_params["customMail"] = args.pop("custom_mail")
        if "rollback_proposals" in args:
            query_params["rollbackProposals"] = args.pop("rollback_proposals")
        
        # Определяем тип торгов
        trading_type = current_proc.get("tradingType")
        
        # Базовые поля - берем из текущей процедуры
        data = {
            "name": args.get("name", current_proc.get("name")),
            "type": args.get("type", current_proc.get("type")),
            "tradingType": trading_type,
            "openType": args.get("open_type", current_proc.get("openType")),
            "currency": args.get("currency", current_proc.get("currency")),
            "acceptanceEndDate": args.get("acceptance_end_date", current_proc.get("acceptanceEndDate")),
            "approximateDeadlineForSummingUp": args.get("approximate_deadline_for_summing_up", current_proc.get("approximateDeadlineForSummingUp", 5)),
            "contacts": args.get("contacts", current_proc.get("contacts")),
            "users": args.get("users", current_proc.get("users")),
            "tags": args.get("tags", current_proc.get("tags", [])),
            "commonFiles": args.get("common_files", current_proc.get("commonFiles", [])),
            "description": args.get("description", current_proc.get("description", "")),
        }
        
        # Для RFI (trading_type=8) - мониторинг рынка
        if trading_type == 8:
            logger.info("Updating RFI procedure...")
            
            # Обязательные поля для RFI
            data["positionsEnabled"] = True
            data["otherParticipantsVisibility"] = args.get("other_participants_visibility", current_proc.get("otherParticipantsVisibility", 3))
            data["betUpDown"] = args.get("bet_up_down", current_proc.get("betUpDown", True))
            data["betStep"] = args.get("bet_step", current_proc.get("betStep", 0.01))
            data["betStepType"] = args.get("bet_step_type", current_proc.get("betStepType", 1))
            data["betReference"] = args.get("bet_reference", current_proc.get("betReference", 0))
            data["betPrice"] = args.get("bet_price", current_proc.get("betPrice", 0))
            data["participantQuestionnaireEnabled"] = False
            data["participantApplicationFiles"] = False
            data["questionnaireEnabled"] = False
            data["ndaEnabled"] = False
            data["vatEnabled"] = False
            data["alternativeProposals"] = 0
            
        
        # Для других типов торгов
        elif trading_type == 1:  # RFP
            data["positionsEnabled"] = True
            data["betUpDown"] = args.get("bet_up_down", current_proc.get("betUpDown", True))
            data["betStep"] = args.get("bet_step", current_proc.get("betStep", 0.01))
            data["betStepType"] = args.get("bet_step_type", current_proc.get("betStepType", 1))
            data["betReference"] = args.get("bet_reference", current_proc.get("betReference", 0))
            data["betPrice"] = args.get("bet_price", current_proc.get("betPrice", 0))
            data["positionGroups"] = current_proc.get("positionGroups", [])
            
            if "positions" in args:
                if data["positionGroups"]:
                    data["positionGroups"][0]["positions"] = args["positions"]
        
        elif trading_type == 4:  # PCO
            data["positionsEnabled"] = False
            data["betStep"] = args.get("bet_step", current_proc.get("betStep", 0.01))
        
        elif trading_type == 2:  # RFQ
            data["positionsEnabled"] = True
            data["betUpDown"] = args.get("bet_up_down", current_proc.get("betUpDown", True))
            data["betStep"] = args.get("bet_step", current_proc.get("betStep", 0.01))
            data["betStepType"] = args.get("bet_step_type", current_proc.get("betStepType", 1))
            data["positionGroups"] = current_proc.get("positionGroups", [])
            
            if "positions" in args and data["positionGroups"]:
                data["positionGroups"][0]["positions"] = args["positions"]
        
        # Добавляем другие поля
        other_fields = {
            "delivery_addresses": "deliveryAddresses",
            "linked_procedures": "linkedProcedures",
            "additional_currencies": "additionalCurrencies",
            "categories": "categories",
            "participant_questionnaire": "participantQuestionnaire",
            "questionnaire": "questionnaire",
            "nda_files": "ndaFiles",
            "nda_description": "ndaDescription",
            "proposal_rank_file": "proposalRankFile",
            "proposal_rank_email": "proposalRankEmail",
            "comment": "comment",
            "budget": "budget",
            "identifier": "identifier",
            "segment_id": "segmentId",
            "submission_start_date": "submissionStartDate",
            "emoji": "emoji"
        }
        
        for field, api_field in other_fields.items():
            if field in args:
                data[api_field] = args[field]
            elif current_proc.get(api_field):
                data[api_field] = current_proc[api_field]
        
        # Булевы поля
        bool_fields = {
            "participant_questionnaire_enabled": "participantQuestionnaireEnabled",
            "participant_application_files": "participantApplicationFiles",
            "questionnaire_enabled": "questionnaireEnabled",
            "nda_enabled": "ndaEnabled",
            "vat_enabled": "vatEnabled"
        }
        
        for field, api_field in bool_fields.items():
            if field in args:
                data[api_field] = args[field]
            elif current_proc.get(api_field) is not None:
                data[api_field] = current_proc[api_field]
        
        # Удаляем None значения
        data = {k: v for k, v in data.items() if v is not None}
        
        # Логируем отправляемые данные
        logger.info(f"📤 Updating RFI procedure {procedure_id}")
        logger.info(f"Positions count: {len(data.get('positionGroups', [{}])[0].get('positions', [])) if data.get('positionGroups') else 0}")
        logger.debug(f"Body: {json.dumps(data, ensure_ascii=False, default=str)[:1000]}")
        
        # Отправляем запрос
        result = client.request("PATCH", f"procedures/{procedure_id}", 
                                params=query_params if query_params else None, 
                                json_data=data)
        
        logger.info(f"✅ Procedure updated: {result.get('id')}")
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to update procedure: {e}", exc_info=True)
        return {"error": str(e)}
async def delete_procedure_draft_handler(args: Dict) -> Dict:
    client.request("DELETE", f"procedures/{args['procedure_id']}")
    return {"success": True, "message": "Draft deleted"}


async def publish_procedure_handler(args: Dict) -> Dict:
    params = {"publishDate": args["publish_date"]} if args.get("publish_date") else None
    return client.request("POST", f"procedures/{args['procedure_id']}/publish", params=params)


async def get_proposals_ids_handler(args: Dict) -> List[str]:
    params = {"sortType": args["sort_type"]} if args.get("sort_type") else None
    return client.request("GET", f"procedures/{args['procedure_id']}/proposals-ids", params=params)


async def get_proposals_handler(args: Dict) -> List[Dict]:
    params = {"ids": args["proposal_ids"]}
    if args.get("with_fake_positions"):
        params["withFakePositions"] = "true"
    return client.request("GET", f"procedures/{args['procedure_id']}/proposals", params=params)


async def get_proposals_ranks_handler(args: Dict) -> Dict:
    return client.request("GET", f"procedures/{args['procedure_id']}/proposals/ranks")


async def rollback_proposal_handler(args: Dict) -> Dict:
    data = {
        "reason": args.get("reason", ""),
        "allowChangePrice": args.get("allow_change_price", False)
    }
    return client.request("POST", f"procedures/{args['procedure_id']}/proposals/{args['proposal_id']}/rollback-proposal", json_data=data)


async def get_participants_handler(args: Dict) -> List[Dict]:
    """Возвращает сырой список участников процедуры."""
    result = client.request("GET", f"procedures/{args['procedure_id']}/participants")
    logger.debug(f"Participants raw: {result}")
    return result


async def invite_participants_handler(args: Dict) -> List[Dict]:
    """Приглашает участников по ИНН или email."""
    return client.request("POST", f"procedures/{args['procedure_id']}/participants/bytinemail", json_data=args["invitations"])


async def find_participant_ids_by_identifiers(procedure_id: str, identifiers: List[str]) -> List[str]:
    """
    Находит UUID участников по названию компании, email, ИНН или UUID.
    Учитывает поля: id, inviteCompanyName, inviteEmail, contactEmail, companyInfo.inn и т.д.
    """
    participants = await get_participants_handler({"procedure_id": procedure_id})
    if not participants:
        logger.warning(f"No participants found in procedure {procedure_id}")
        return []

    found_ids = []
    not_found = []

    for identifier in identifiers:
        identifier_lower = identifier.lower().strip()
        found = False

        for p in participants:
            # Извлекаем возможные идентификаторы
            p_id = p.get("id", "")
            p_name = p.get("inviteCompanyName", "") or p.get("companyName", "") or p.get("name", "") or ""
            p_email = p.get("inviteEmail", "") or p.get("contactEmail", "") or p.get("email", "") or ""
            p_inn = ""
            # Если есть companyInfo, можно попробовать взять ИНН оттуда
            company_info = p.get("companyInfo")
            if company_info and isinstance(company_info, dict):
                p_inn = company_info.get("inn", "") or company_info.get("taxId", "")

            # Проверка совпадений
            if (identifier_lower == p_id.lower() or
                identifier_lower == p_name.lower() or
                identifier_lower == p_email.lower() or
                (p_inn and identifier_lower == p_inn.lower()) or
                identifier in p_id or                     # частичное совпадение UUID
                p_name.lower().find(identifier_lower) != -1 or
                p_email.lower().find(identifier_lower) != -1):
                found_ids.append(p_id)
                found = True
                logger.info(f"Found participant: {p_name} (ID: {p_id}, Email: {p_email})")
                break

        if not found:
            not_found.append(identifier)
            logger.warning(f"Participant not found: {identifier}")

    if not_found:
        logger.warning(f"Could not find participants: {not_found}")

    return found_ids


async def block_participants_handler(args: Dict) -> Dict:
    procedure_id = args["procedure_id"]
    identifiers = args["participant_ids"]
    block_reason = args.get("block_reason", "")

    logger.info(f"🔒 Blocking participants in procedure {procedure_id}: {identifiers}")

    try:
        participant_uuids = await find_participant_ids_by_identifiers(procedure_id, identifiers)
        if not participant_uuids:
            return {
                "success": False,
                "error": "No participants found",
                "message": f"Could not find any participants matching: {identifiers}",
                "provided_identifiers": identifiers
            }

        data = {
            "participantIds": participant_uuids,
            "blockReason": block_reason
        }
        result = client.request("PUT", f"procedures/{procedure_id}/participants/block", json_data=data)

        if result and isinstance(result, dict) and result.get("success") is False:
            return result

        logger.info(f"✅ Successfully blocked {len(participant_uuids)} participants: {participant_uuids}")
        return {
            "success": True,
            "message": f"Successfully blocked {len(participant_uuids)} participant(s)",
            "blocked_participants": participant_uuids,
            "block_reason": block_reason,
            "procedure_id": procedure_id
        }

    except Exception as e:
        logger.error(f"Error blocking participants: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Blocking failed",
            "message": str(e),
            "procedure_id": procedure_id,
            "identifiers": identifiers
        }


async def unblock_participants_handler(args: Dict) -> Dict:
    procedure_id = args["procedure_id"]
    identifiers = args["participant_ids"]

    logger.info(f"🔓 Unblocking participants in procedure {procedure_id}: {identifiers}")

    try:
        participant_uuids = await find_participant_ids_by_identifiers(procedure_id, identifiers)
        if not participant_uuids:
            return {
                "success": False,
                "error": "No participants found",
                "message": f"Could not find any participants matching: {identifiers}",
                "provided_identifiers": identifiers
            }

        result = client.request("PUT", f"procedures/{procedure_id}/participants/unblock", json_data=participant_uuids)

        if result and isinstance(result, dict) and result.get("success") is False:
            return result

        logger.info(f"✅ Successfully unblocked {len(participant_uuids)} participants: {participant_uuids}")
        return {
            "success": True,
            "message": f"Successfully unblocked {len(participant_uuids)} participant(s)",
            "unblocked_participants": participant_uuids,
            "procedure_id": procedure_id
        }

    except Exception as e:
        logger.error(f"Error unblocking participants: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Unblocking failed",
            "message": str(e),
            "procedure_id": procedure_id,
            "identifiers": identifiers
        }


async def get_blocked_participants_handler(args: Dict) -> List[Dict]:
    procedure_id = args["procedure_id"]
    logger.info(f"📋 Getting blocked participants for procedure {procedure_id}")

    try:
        result = client.request("GET", f"procedures/{procedure_id}/participants/blocked")
        if result and isinstance(result, dict) and result.get("success") is False:
            return result
        logger.info(f"✅ Found {len(result) if isinstance(result, list) else 0} blocked participants")
        return result
    except Exception as e:
        logger.error(f"Error getting blocked participants: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Failed to get blocked participants",
            "message": str(e),
            "procedure_id": procedure_id
        }


async def get_participants_with_details_handler(args: Dict) -> Dict:
    procedure_id = args["procedure_id"]
    search = args.get("search", "").strip()

    logger.info(f"📋 Getting participants for procedure {procedure_id}" + (f" with search: {search}" if search else ""))

    try:
        participants = await get_participants_handler({"procedure_id": procedure_id})
        if not participants:
            return {
                "success": True,
                "participants": [],
                "total": 0,
                "message": "No participants found in this procedure"
            }

        if search:
            search_lower = search.lower()
            filtered = []
            for p in participants:
                name = p.get("inviteCompanyName", "") or p.get("companyName", "") or ""
                email = p.get("inviteEmail", "") or p.get("contactEmail", "") or ""
                pid = p.get("id", "")
                inn = ""
                company_info = p.get("companyInfo")
                if company_info and isinstance(company_info, dict):
                    inn = company_info.get("inn", "") or company_info.get("taxId", "")
                if (search_lower in name.lower() or
                    search_lower in email.lower() or
                    search_lower in pid.lower() or
                    (inn and search_lower in inn.lower())):
                    filtered.append(p)
            participants = filtered

        formatted = []
        for p in participants:
            name = p.get("inviteCompanyName", "") or p.get("companyName", "") or p.get("name", "") or "Unknown"
            email = p.get("inviteEmail", "") or p.get("contactEmail", "") or p.get("email", "")
            inn = ""
            company_info = p.get("companyInfo")
            if company_info and isinstance(company_info, dict):
                inn = company_info.get("inn", "") or company_info.get("taxId", "")
            formatted.append({
                "id": p.get("id"),
                "name": name,
                "inn": inn,
                "email": email,
                "status": p.get("businessStatus", "active"),
                "is_blocked": p.get("isBlocked", False),
                "registered_at": p.get("businessStatusDate") or p.get("createdAt")
            })

        return {
            "success": True,
            "participants": formatted,
            "total": len(formatted),
            "procedure_id": procedure_id,
            "search_used": search if search else None
        }

    except Exception as e:
        logger.error(f"Error getting participants with details: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Failed to get participants",
            "message": str(e),
            "procedure_id": procedure_id
        }
async def approve_participants_handler(args: Dict) -> Dict:
    data = {"participants": args["participant_ids"], "comment": args.get("comment")}
    if args.get("expired_date"):
        data["expiredDate"] = args["expired_date"]
    client.request("PUT", f"procedures/{args['procedure_id']}/participants/accept", json_data=data)
    return {"success": True}


async def reject_participants_handler(args: Dict) -> Dict:
    data = {"participants": args["participant_ids"], "comment": args.get("comment")}
    client.request("PUT", f"procedures/{args['procedure_id']}/participants/reject", json_data=data)
    return {"success": True}



async def get_events_handler(args: Dict) -> List[Dict]:
    params = {}
    if args.get("date_from"):
        params["DateFrom"] = args["date_from"]
    if args.get("event_types"):
        params["Type"] = args["event_types"]
    if args.get("procedure_id"):
        params["ProcedureId"] = args["procedure_id"]
    if args.get("stage_id"):
        params["StageId"] = args["stage_id"]
    return client.request("GET", "events", params=params)


async def complete_with_winners_handler(args: Dict) -> Dict:
    data = {
        "winnerMessage": args.get("winner_message"),
        "looserMessage": args.get("looser_message")
    }
    client.request("POST", f"procedures/{args['procedure_id']}/proposals/set-winners", json_data=args["choices"])
    return client.request("POST", f"procedures/{args['procedure_id']}/complete-with-winners", json_data=data)


async def complete_without_winners_handler(args: Dict) -> Dict:
    data = {"reason": args.get("reason"), "message": args.get("message")}
    return client.request("POST", f"procedures/{args['procedure_id']}/complete-without-winners", json_data=data)


async def finish_proposals_acceptance_handler(args: Dict) -> Dict:
    return client.request("PUT", f"procedures/{args['procedure_id']}/finish-round")


async def get_stages_handler(args: Dict) -> Dict:
    return client.request("GET", f"procedures/{args['procedure_id']}/stages")


async def get_stages_full_info_handler(args: Dict) -> Dict:
    return client.request("GET", f"procedures/{args['procedure_id']}/stages/full-info")


async def announce_new_stage_handler(args: Dict) -> Dict:
    params = {}
    if args.get("owner_comment"):
        params["ownerComment"] = args["owner_comment"]
    if args.get("custom_mail"):
        params["customMail"] = args["custom_mail"]
    if args.get("rollback_proposals"):
        params["rollbackProposals"] = args["rollback_proposals"]
    if args.get("publish_date"):
        params["publishDate"] = args["publish_date"]
    
    data = {"id": args["procedure_id"], "name": args.get("owner_comment", "New Stage")}
    return client.request("POST", f"procedures/{args['procedure_id']}/new-stage", params=params, json_data=data)


async def get_choices_handler(args: Dict) -> List[Dict]:
    return client.request("GET", f"procedures/{args['procedure_id']}/proposals/choices")


async def set_winners_handler(args: Dict) -> Dict:
    client.request("POST", f"procedures/{args['procedure_id']}/proposals/set-winners", json_data=args["choices"])
    return {"success": True}


async def get_companies_info_handler(args: Dict) -> List[Dict]:
    params = {"ids": args["company_ids"]}
    return client.request("GET", "companies-info", params=params)


async def get_segments_handler(args: Dict) -> Dict:
    params = {"search": args["search"]} if args.get("search") else None
    return client.request("GET", "segments", params=params)


async def get_tags_handler(args: Dict) -> List[str]:
    params = {}
    if args.get("search_criteria"):
        params["searchCriteria"] = args["search_criteria"]
    if args.get("usage_count"):
        params["usageCount"] = args["usage_count"]
    if args.get("page"):
        params["page"] = args["page"]
    if args.get("size"):
        params["size"] = args["size"]
    return client.request("GET", "tags", params=params)


async def get_special_conditions_handler(args: Dict) -> List[str]:
    return client.request("GET", "tariffs/special")


async def get_chat_spaces_handler(args: Dict) -> List[Dict]:
    return client.request("GET", "chat-spaces")


async def get_chats_handler(args: Dict) -> List[Dict]:
    return client.request("GET", f"chat-spaces/{args['space_id']}/chats")


async def send_chat_message_handler(args: Dict) -> Dict:
    data = {
        "content": args["content"],
        "files": [{"id": fid} for fid in args.get("file_ids", [])]
    }
    return client.request("POST", f"chat-spaces/{args['space_id']}/chats/{args['chat_id']}/message", json_data=data)


async def upload_files_handler(args: Dict) -> List[Dict]:
    """
    Upload files to Bidzaar storage.
    Supports formats: doc, docx, xls, xlsx, pdf, txt, csv, pptx, dwg, jpg, png, gif, bmp, tiff, svg, webp, zip, rar, 7z.
    
    Args:
        args: {
            "files": [
                {
                    "name": "filename.pdf",
                    "extension": "pdf",
                    "base64": "base64_encoded_content"  # опционально
                },
                {
                    "name": "document.pdf", 
                    "extension": "pdf",
                    "file_path": "/path/to/file.pdf"  # альтернативно: путь к файлу
                }
            ]
        }
    """
    
    logger.info(f"📤 Uploading {len(args['files'])} file(s)")
    
    files_base_path = settings_env.bidzaar_files_base_path
    logger.debug(f"  Reading .env path: {files_base_path}")
    try:
        client._ensure_token()
        
        files = []
        for f in args["files"]:
            file_data = None
            file_name = f.get("name")
            file_extension = f.get("extension", "")
            
            # base64 контент
            if "base64" in f and f["base64"]:
                logger.debug(f"  Using base64 for: {file_name}")
                file_data = base64.b64decode(f["base64"])
            
            # ссылка на файл (file_path)
            elif "file_path" in f and f["file_path"]:
                file_path = f"{files_base_path}/{f['file_path']}"
                logger.debug(f"  Reading from path: {files_base_path}/{file_path}")
                
                
                if not os.path.exists(file_path):
                    
                    alt_paths = [
                        file_path,
                        f"{file_path}",
                        f"{files_base_path}/{Path(file_path).name}",
                    ]
                    
                    found = False
                    for alt_path in alt_paths:
                        if os.path.exists(alt_path):
                            file_path = alt_path
                            found = True
                            logger.debug(f"    Found at: {file_path}")
                            break
                    
                    if not found:
                        raise Exception(f"File not found: {file_path} (searched in: {alt_paths})")
                
                with open(file_path, 'rb') as file_obj:
                    file_data = file_obj.read()
                
                if not file_name:
                    file_name = Path(file_path).name
                    logger.debug(f"    Auto-detected name: {file_name}")
            
            elif "content" in f and f["content"]:
                logger.warning(f"  Using deprecated 'content' field for: {file_name}")
                file_data = base64.b64decode(f["content"])
            
            else:
                raise Exception(f"No content provided for file: {file_name}. Use 'base64' or 'file_path'")
            
            if not file_name:
                raise Exception(f"File name is required for file: {f}")
            
            mime_type = f.get("mime_type")
            if not mime_type:
                ext = file_extension.lower() if file_extension else Path(file_name).suffix.lower().lstrip('.')
                mime_types = {
                    'pdf': 'application/pdf',
                    'doc': 'application/msword',
                    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'xls': 'application/vnd.ms-excel',
                    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'txt': 'text/plain',
                    'csv': 'text/csv',
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                    'png': 'image/png',
                    'gif': 'image/gif',
                    'zip': 'application/zip',
                    'rar': 'application/x-rar-compressed',
                    '7z': 'application/x-7z-compressed',
                }
                mime_type = mime_types.get(ext, 'application/octet-stream')
            
            files.append(("files", (file_name, file_data, mime_type)))
            logger.debug(f"   File: {file_name}, size: {len(file_data)} bytes, type: {mime_type}")
        

        headers = {
            'Authorization': f'Bearer {client.access_token}',
            'X-Bidzaar-Connector-User-Email': client.config.user_email
        }
        
        url = client._get_api_url("files/upload")
        
        response = client.session.post(
            url, 
            files=files, 
            headers=headers,
            timeout=60
        )
        
        if response.status_code == 401:
            logger.warning("⚠️ Token expired, refreshing...")
            client._refresh_token()
            headers['Authorization'] = f'Bearer {client.access_token}'
            response = client.session.post(url, files=files, headers=headers, timeout=60)
        
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"✅ Files uploaded successfully: {len(result)} file(s)")
        return result
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP Error: {e}")
        if e.response is not None:
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response body: {e.response.text}")
        return {"error": str(e), "status_code": e.response.status_code if e.response else None}
    except Exception as e:
        logger.error(f"Error uploading files: {e}", exc_info=True)
        return {"error": str(e)}


async def get_file_handler(args: Dict) -> str:
    """Get file as base64"""
    import base64
    response = client.request("GET", f"files/{args['file_id']}", raw_response=True)
    return base64.b64encode(response.content).decode('utf-8')


async def get_comparison_file_handler(args: Dict) -> Dict:
    params = {"sortType": args.get("sort_type", 0)}
    return client.request("GET", f"procedures/{args['procedure_id']}/comparison-file", params=params)


async def get_report_file_handler(args: Dict) -> str:
    import base64
    response = client.request("GET", f"procedures/{args['procedure_id']}/reports/{args['report_id']}", raw_response=True)
    return base64.b64encode(response.content).decode('utf-8')


async def get_participant_applications_handler(args: Dict) -> List[Dict]:
    return client.request("GET", f"procedures/{args['procedure_id']}/participants/{args['participant_id']}/applications")


async def request_documents_handler(args: Dict) -> Dict:
    data = {
        "participantIds": args["participant_ids"],
        "endDate": args["end_date"],
        "comment": args.get("comment")
    }
    return client.request("POST", f"procedures/{args['procedure_id']}/participants/request-documents", json_data=data)


async def apply_promo_code_handler(args: Dict) -> bool:
    data = {"promoCode": args["promo_code"]}
    return client.request("POST", f"procedures/{args['procedure_id']}/commissions/promo-code", json_data=data)


async def add_additional_currency_handler(args: Dict) -> Dict:
    data = {
        "additionalCurrencies": [{
            "currency": args["currency"],
            "amount": args["amount"],
            "rate": args["rate"]
        }]
    }
    return client.request("POST", f"procedures/{args['procedure_id']}/additional-currencies", json_data=data)


async def update_additional_currencies_handler(args: Dict) -> Dict:
    data = {"additionalCurrencies": args["additional_currencies"]}
    return client.request("PUT", f"procedures/{args['procedure_id']}/additional-currencies", json_data=data)


async def cancel_delayed_publication_handler(args: Dict) -> Dict:
    return client.request("POST", f"procedures/{args['procedure_id']}/cancel-delayed-publication")


async def cancel_stage_handler(args: Dict) -> bool:
    return client.request("POST", f"procedures/{args['procedure_id']}/stages/cancel")


async def improve_description_handler(args: Dict) -> Dict:
    data = {
        "procedureId": args["procedure_id"],
        "description": args["description"]
    }
    return client.request("POST", "procedures/improve-description", json_data=data)


async def return_to_evaluation_handler(args: Dict) -> Dict:
    data = {
        "reason": args.get("reason"),
        "subject": args.get("subject"),
        "message": args.get("message")
    }
    return client.request("PUT", f"procedures/{args['procedure_id']}/uncomplete", json_data=data)


async def allow_price_change_handler(args: Dict) -> None:
    client.request("PUT", f"procedures/{args['procedure_id']}/participants/{args['participant_id']}/price-change")
    return {"success": True}


async def reject_price_change_request_handler(args: Dict) -> None:
    client.request("DELETE", f"procedures/{args['procedure_id']}/participants/{args['participant_id']}/price-change")
    return {"success": True}


# ============================================================================
# MCP CALL HANDLER
# ============================================================================

@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[types.TextContent]:
    try:
        result = await execute_tool(name, arguments)

        if isinstance(result, (dict, list)):
            text = json.dumps(result, ensure_ascii=False)
        else:
            text = str(result)
        return [types.TextContent(type="text", text=text)]
    except Exception as e:
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]

# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Main entry point"""
    logger.info("Starting Bidzaar MCP Server (stdio)")
    logger.info(f"Bidzaar API: {settings_env.bidzaar_base_url}/api/connector/v{settings_env.bidzaar_api_version}")
    logger.info(f"User: {settings_env.bidzaar_user_email}")
    
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="bidzaar-mcp-server",
                server_version="1.0.0",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())

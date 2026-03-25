# -*- coding: utf-8 -*-
"""
Created on Fri Jan 13 17:42:41 2026

@author: rublev.an
"""
"""
MCP Server for Bidzaar Connector API (stdio) v 0.1
Implements Bidzaar API v5.2
"""

import os
import sys
import json
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
import requests

from pydantic_settings import BaseSettings, SettingsConfigDict

# MCP imports
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

# ============================================================================
# CONFIGURATION
# ============================================================================


class Settings_env(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    bidzaar_client_id: str
    bidzaar_base_url: str
    bidzaar_client_secret: str
    bidzaar_api_version: str
    bidzaar_user_email: str
    
settings_env = Settings_env()


logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("bidzaar-mcp-server")


@dataclass
class BidzaarConfig:
    base_url: str = settings_env.bidzaar_base_url
    client_id: str = settings_env.bidzaar_client_id
    client_secret: str = settings_env.bidzaar_client_secret
    user_email: str = settings_env.bidzaar_user_email
    api_version: str = settings_env.bidzaar_api_version
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


# MCP Server

app = Server("bidzaar-mcp-server")
client = BidzaarClient(BidzaarConfig())

# TOOLs: All Methods from v5.2 API

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """List all available Bidzaar API tools"""
    return [
        # ========== PROCEDURE MANAGEMENT ==========
        types.Tool(
            name="create_procedure",
            description="Create a new procurement procedure on Bidzaar platform. Supports draft creation or immediate publishing. Required fields: name, type (1=procurement, 2=sale, 3=registry), trading_type (1=fixed_volume (rfp), 2=per_unit (rfq), 4=PCO (qulification), 8=market_monitoring (rfi), 16=registry). Optional: positions array with name, count, unit, price.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Procedure name, max 600 chars"},
                    "type": {"type": "integer", "description": "Procedure type: 1=procurement, 2=sale, 3=registry"},
                    "trading_type": {"type": "integer", "description": "Trading type: 1=fixed_volume (rfp), 2=per_unit (rfp unit), 4=PCO (qualification), 8=market_monitoring (rfi), 16=registry"},
                    "description": {"type": "string", "description": "HTML description, max 4088 chars"},
                    "open_type": {"type": "integer", "description": "0=open (all suppliers), 1=closed (invited only)", "default": 0},
                    "currency": {"type": "string", "description": "Currency: RUB, USD, EUR, etc", "default": "RUB"},
                    "acceptance_end_date": {"type": "string", "description": "ISO 8601 end date for proposal submission"},
                    "positions": {"type": "array", "items": {"type": "object"}, "description": "Array of positions with name, count, unit, price"},
                    "publish_immediately": {"type": "boolean", "description": "Publish immediately or create draft", "default": True}
                },
                "required": ["name", "type", "trading_type"]
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
            description="Update existing procedure parameters. Can modify name, description, end date, positions, etc. For published procedures, changes will cause republication. Use custom_mail to notify participants.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure to update"},
                    "name": {"type": "string", "description": "New procedure name"},
                    "description": {"type": "string", "description": "New HTML description"},
                    "acceptance_end_date": {"type": "string", "description": "New end date ISO 8601"},
                    "custom_mail": {"type": "string", "description": "Message to send to all participants"},
                    "rollback_proposals": {"type": "boolean", "description": "Whether to reject existing proposals"}
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
        
        # ========== PROPOSAL MANAGEMENT ==========
        types.Tool(
            name="get_proposals_ids",
            description="Get list of proposal UUIDs for a procedure. Sort options: 0=by price (lowest first), 1=by selection, 2=by satisfaction, 3=by rank, 4=by last update.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "sort_type": {"type": "integer", "description": "Sort type: 0=price, 1=selection, 2=satisfaction, 3=rank, 4=update", "default": 0}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="get_proposals",
            description="Get detailed information about specific proposals by their UUIDs. Returns complete proposal data including prices, positions, participant info, and attachments.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "proposal_ids": {"type": "array", "items": {"type": "string"}, "description": "List of proposal UUIDs (max 30)"},
                    "with_fake_positions": {"type": "boolean", "description": "Include fake positions", "default": False}
                },
                "required": ["procedure_id", "proposal_ids"]
            }
        ),
        types.Tool(
            name="get_proposals_ranks",
            description="Get calculated ranks for all proposals in a procedure. Returns ranking information for groups and individual items.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="rollback_proposal",
            description="Reject a specific proposal. Can optionally allow participant to change price after rejection. Requires organizer permissions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "proposal_id": {"type": "string", "description": "UUID of the proposal to reject"},
                    "reason": {"type": "string", "description": "Reason for rejection"},
                    "allow_change_price": {"type": "boolean", "description": "Allow participant to change price", "default": False}
                },
                "required": ["procedure_id", "proposal_id"]
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
            description="Block participants from submitting proposals. Blocked participants cannot submit and their existing proposals are deleted. Available in 'Proposal Submission' and 'Evaluation' statuses.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "participant_ids": {"type": "array", "items": {"type": "string"}, "description": "List of participant UUIDs to block"},
                    "block_reason": {"type": "string", "description": "Reason for blocking (visible to participant)"}
                },
                "required": ["procedure_id", "participant_ids"]
            }
        ),
        types.Tool(
            name="unblock_participants",
            description="Unblock previously blocked participants. After unblocking, they can submit proposals again.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "participant_ids": {"type": "array", "items": {"type": "string"}, "description": "List of participant UUIDs to unblock"}
                },
                "required": ["procedure_id", "participant_ids"]
            }
        ),
        types.Tool(
            name="approve_participants",
            description="Approve participants for procedures with application requirements. For registry type procedures, can set expiration date for approval.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "participant_ids": {"type": "array", "items": {"type": "string"}, "description": "List of participant UUIDs to approve"},
                    "comment": {"type": "string", "description": "Approval comment"},
                    "expired_date": {"type": "string", "description": "Expiration date for approval (ISO 8601, registry only)"}
                },
                "required": ["procedure_id", "participant_ids"]
            }
        ),
        types.Tool(
            name="reject_participants",
            description="Reject participants and request new applications. Available for procedures with application requirements.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "participant_ids": {"type": "array", "items": {"type": "string"}, "description": "List of participant UUIDs to reject"},
                    "comment": {"type": "string", "description": "Rejection reason visible to participant"}
                },
                "required": ["procedure_id", "participant_ids"]
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
        
        # ========== PROCEDURE COMPLETION ==========
        types.Tool(
            name="complete_with_winners",
            description="Complete procedure with selected winners. Must call set_winners first to select winning proposals.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "choices": {"type": "array", "items": {"type": "object"}, "description": "Selected winning proposals"},
                    "winner_message": {"type": "string", "description": "Message to winners"},
                    "looser_message": {"type": "string", "description": "Message to non-winners"}
                },
                "required": ["procedure_id", "choices"]
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
        types.Tool(
            name="finish_proposals_acceptance",
            description="Finish proposal acceptance early. Transitions procedure from 'Proposal Submission' to 'Evaluation' status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        
        # ========== STAGES ==========
        types.Tool(
            name="get_stages",
            description="Get list of stage IDs for a procedure with their dates and comments.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="get_stages_full_info",
            description="Get complete information about all stages including versions, participants, chats, and proposals.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="announce_new_stage",
            description="Announce a new stage for the procedure (e.g., rebidding). Can update procedure parameters for the new stage.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "owner_comment": {"type": "string", "description": "Stage name/comment"},
                    "custom_mail": {"type": "string", "description": "Message to participants"},
                    "rollback_proposals": {"type": "boolean", "description": "Reject existing proposals"},
                    "publish_date": {"type": "string", "description": "Delayed publication date ISO 8601"}
                },
                "required": ["procedure_id"]
            }
        ),
        
        # ========== WINNER SELECTION ==========
        types.Tool(
            name="get_choices",
            description="Get previously made winner selections for a procedure. Returns which proposals were selected and for which items.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="set_winners",
            description="Select winners from participant proposals. Can select entire proposals, groups, or individual positions with quantities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "choices": {"type": "array", "items": {"type": "object"}, "description": "List of selected proposals with proposalId, originId (for positions), and count"}
                },
                "required": ["procedure_id", "choices"]
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
            name="get_segments",
            description="Get business segments available for the company. Can search by segment name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Search by segment name"}
                }
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
        types.Tool(
            name="get_special_conditions",
            description="Get list of special conditions available for the company. Used for custom contract terms.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        
        ========== CHAT ==========
        types.Tool(
            name="get_chat_spaces",
            description="Get all chat spaces for the company. Each space corresponds to a procedure.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="get_chats",
            description="Get all chats within a specific chat space (procedure). Returns chat IDs and types.",
            inputSchema={
                "type": "object",
                "properties": {
                    "space_id": {"type": "string", "description": "UUID of the chat space"}
                },
                "required": ["space_id"]
            }
        ),
        types.Tool(
            name="send_chat_message",
            description="Send a message to a specific chat. Files must be uploaded first using upload_files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "space_id": {"type": "string", "description": "UUID of the chat space"},
                    "chat_id": {"type": "string", "description": "UUID of the chat"},
                    "content": {"type": "string", "description": "Message text"},
                    "file_ids": {"type": "array", "items": {"type": "string"}, "description": "UUIDs of uploaded files"}
                },
                "required": ["space_id", "chat_id", "content"]
            }
        ),
        
        # ========== FILE MANAGEMENT ==========
        types.Tool(
            name="upload_files",
            description="Upload files to company storage. Supports formats: doc, docx, xls, xlsx, pdf, txt, csv, pptx, dwg, jpg, png, gif, bmp, tiff, svg, webp, zip, rar, 7z. Returns file IDs for use in other operations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "files": {"type": "array", "items": {"type": "object"}, "description": "List of files with name, extension, and base64 content"}
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
        
        # ========== PROMO CODES ==========
        types.Tool(
            name="apply_promo_code",
            description="Apply a promo code to a procedure. Valid for procedures that support commission discounts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "promo_code": {"type": "string", "description": "Promo code to apply"}
                },
                "required": ["procedure_id", "promo_code"]
            }
        ),
        
        # ========== ADDITIONAL CURRENCIES ==========
        types.Tool(
            name="add_additional_currency",
            description="Add additional currency to a procedure with amount and exchange rate. For multi-currency procedures.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "currency": {"type": "string", "description": "Currency code (USD, EUR, etc)"},
                    "amount": {"type": "number", "description": "Amount in additional currency"},
                    "rate": {"type": "number", "description": "Exchange rate to base currency"}
                },
                "required": ["procedure_id", "currency", "amount", "rate"]
            }
        ),
        types.Tool(
            name="update_additional_currencies",
            description="Update additional currencies for a procedure. Can update amounts and rates for existing currencies.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "additional_currencies": {"type": "array", "items": {"type": "object"}, "description": "List of currencies with amounts and rates"}
                },
                "required": ["procedure_id", "additional_currencies"]
            }
        ),
        
        ========== CANCELLATION ==========
        types.Tool(
            name="cancel_delayed_publication",
            description="Cancel scheduled delayed publication of a procedure or stage.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        types.Tool(
            name="cancel_stage",
            description="Cancel the current stage of a procedure. Only available for stage workflows.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"}
                },
                "required": ["procedure_id"]
            }
        ),
        
        # ========== AI DESCRIPTION IMPROVEMENT ==========
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
        
        # ========== PERMISSION MANAGEMENT ==========
        types.Tool(
            name="allow_price_change",
            description="Allow a participant to change proposal price despite rule violations. Can be granted on request or proactively.",
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
            name="reject_price_change_request",
            description="Reject a participant's request to change proposal price with rule violation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                    "participant_id": {"type": "string", "description": "UUID of the participant"}
                },
                "required": ["procedure_id", "participant_id"]
            }
        ),
    ]


# TOOL HANDLERS
HANDLERS = {
    "create_procedure": create_procedure_handler,
    "get_procedure": get_procedure_handler,
    "update_procedure": update_procedure_handler,
    "delete_procedure_draft": delete_procedure_draft_handler,
    "publish_procedure": publish_procedure_handler,
    "get_proposals_ids": get_proposals_ids_handler,
    "get_proposals": get_proposals_handler,
    "get_proposals_ranks": get_proposals_ranks_handler,
    "rollback_proposal": rollback_proposal_handler,
    "get_participants": get_participants_handler,
    "invite_participants": invite_participants_handler,
    "block_participants": block_participants_handler,
    "unblock_participants": unblock_participants_handler,
    "approve_participants": approve_participants_handler,
    "reject_participants": reject_participants_handler,
    "get_blocked_participants": get_blocked_participants_handler,
    "get_events": get_events_handler,
    "complete_with_winners": complete_with_winners_handler,
    "complete_without_winners": complete_without_winners_handler,
    "finish_proposals_acceptance": finish_proposals_acceptance_handler,
    "get_stages": get_stages_handler,
    "get_stages_full_info": get_stages_full_info_handler,
    "announce_new_stage": announce_new_stage_handler,
    "get_choices": get_choices_handler,
    "set_winners": set_winners_handler,
    "get_companies_info": get_companies_info_handler,
    "get_segments": get_segments_handler,
    "get_tags": get_tags_handler,
    "get_special_conditions": get_special_conditions_handler,
    "get_chat_spaces": get_chat_spaces_handler,
    "get_chats": get_chats_handler,
    "send_chat_message": send_chat_message_handler,
    "upload_files": upload_files_handler,
    "get_file": get_file_handler,
    "get_comparison_file": get_comparison_file_handler,
    "get_report_file": get_report_file_handler,
    "get_participant_applications": get_participant_applications_handler,
    "request_documents": request_documents_handler,
    "apply_promo_code": apply_promo_code_handler,
    "add_additional_currency": add_additional_currency_handler,
    "update_additional_currencies": update_additional_currencies_handler,
    "cancel_delayed_publication": cancel_delayed_publication_handler,
    "cancel_stage": cancel_stage_handler,
    "improve_description": improve_description_handler,
    "return_to_evaluation": return_to_evaluation_handler,
    "allow_price_change": allow_price_change_handler,
    "reject_price_change_request": reject_price_change_request_handler,
}

async def execute_tool(tool_name: str, arguments: Dict) -> Any:
    """Execute tool with given arguments"""
    logger.info(f"🔧 Executing tool: {tool_name}")
    logger.debug(f"Arguments: {json.dumps(arguments, ensure_ascii=False, default=str)}")
    try:
        handler = HANDLERS.get(tool_name)
        if handler:
            return await handler(arguments)
        return {"error": f"Unknown tool: {tool_name}"}        
    except Exception as e:
        logger.exception(f"Error executing {tool_name}")
        return {"error": str(e)}


# HANDLER IMPLEMENTATIONS

async def create_procedure_handler(args: Dict) -> Dict:
    """
    Create procedure handler with correct field handling for different trading types
    """
    logger.info(f"Creating procedure: {args.get('name')}")
    
    try:
        trading_type = args.get("trading_type", 8)
        is_draft = args.get("publish_immediately") is False
        has_positions = bool(args.get("positions"))
        
        # БАЗОВЫЕ ОБЯЗАТЕЛЬНЫЕ ПОЛЯ
        data = {
            "type": args.get("type", 1),
            "tradingType": trading_type,
            "name": args.get("name"),
            "openType": args.get("open_type", 0),
            "currency": args.get("currency", "RUB"),
            "acceptanceEndDate": args.get("acceptance_end_date") or (
                (datetime.now(timezone.utc) + timedelta(days=args.get("acceptance_end_days", 7))).isoformat()
            ),
            "ApproximateDeadlineForSummingUp": args.get("approximate_deadline_for_summing_up", 5),
            "contacts": args.get("contacts", f"Контактное лицо: {settings_env.bidzaar_user_email}"),
            "users": [
                {
                    "email": settings_env.bidzaar_user_email,
                    "role": 2,
                    "isResponsible": True,
                    "isResponsibleForApplications": True
                }
            ]
        }
        
        # Для торгов с позициями (tradingType: 1, 2, 8)
        if trading_type in [1, 2, 8] and has_positions:
            data["positionsEnabled"] = True
            data["positionGroups"] = []
            
            # Группируем позиции (можно сделать несколько групп, пока одна)
            group = {
                "originId": str(uuid.uuid4()),
                "name": "Основная группа",
                "deviationType": 0,
                "betStep": args.get("bet_step", 0.1),
                "additionalFields": args.get("additional_fields", []),
                "positions": []
            }
            
            for p in args["positions"]:
                position = {
                    "originId": str(uuid.uuid4()),
                    "name": p.get("name", "Товар"),
                    "count": float(p.get("count", 1)),
                    "unit": p.get("unit", "шт."),
                    "price": float(p.get("price", 0)),
                    "additionalFieldsValues": p.get("additional_fields_values", [])
                }
                group["positions"].append(position)
            
            data["positionGroups"].append(group)
        
        # Для ПКО (tradingType=4) - без позиций
        elif trading_type == 4:
            data["positionsEnabled"] = False
            data["BetStep"] = args.get("bet_step", 0.1)
        
        # Для торгов заданного объема без позиций (tradingType=1 без позиций)
        elif trading_type == 1 and not has_positions:
            data["positionsEnabled"] = False
            data["BetStep"] = args.get("bet_step", 0.1)
        
        # Для реестра (tradingType=16)
        elif trading_type == 16:
            data["positionsEnabled"] = False
            data["participantQuestionnaireEnabled"] = True
            data["participantQuestionnaire"] = [{
                "originId": str(uuid.uuid4()),
                "text": "Основная информация",
                "items": [{
                    "originId": str(uuid.uuid4()),
                    "text": "Согласие на обработку данных",
                    "type": "agreement",
                    "agreementText": "Я согласен на обработку моих персональных данных",
                    "required": True,
                    "private": False,
                    "canAttachFiles": False,
                    "canLeaveComment": False,
                    "files": []
                }]
            }]
        
        elif trading_type == 8 and not has_positions:
            data["positionsEnabled"] = False
            data["otherParticipantsVisibility"] = 3
      
        if args.get("tags"):
            data["tags"] = args["tags"]
        
        # дополнительные поля для торгов заданного объема
        if trading_type == 1:
            data["betUpDown"] = args.get("bet_up_down", True)
            data["betStep"] = args.get("bet_step", 0.01)
            data["betStepType"] = args.get("bet_step_type", 1)
            data["betReference"] = args.get("bet_reference", 0)
            data["betPrice"] = args.get("bet_price", 0)
        
        logger.info(f"Sending to API: {json.dumps(data, ensure_ascii=False, default=str)[:1000]}")
        
        if is_draft:
            endpoint = "procedures/draft"
            logger.info("Creating draft procedure...")
        else:
            endpoint = "procedures/create-publish"
            logger.info("Creating and publishing procedure...")
        
        result = client.request("POST", endpoint, json_data=data)
        
        logger.info(f"Procedure created: {result.get('id')}")
        return result
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP Error: {e}")
        if e.response is not None:
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response body: {e.response.text}")
        return {
            "error": str(e),
            "status_code": e.response.status_code if e.response else None,
            "response_body": e.response.text if e.response else None
        }
    except Exception as e:
        logger.error(f"Failed to create procedure: {e}", exc_info=True)
        return {"error": str(e)}

async def get_procedure_handler(args: Dict) -> Dict:
    return client.request("GET", f"procedures/{args['procedure_id']}")

async def update_procedure_handler(args: Dict) -> Dict:
    procedure_id = args.pop("procedure_id")
    params = {}
    if "custom_mail" in args:
        params["customMail"] = args.pop("custom_mail")
    if "rollback_proposals" in args:
        params["rollbackProposals"] = args.pop("rollback_proposals")
    return client.request("PATCH", f"procedures/{procedure_id}", params=params if params else None, json_data=args)

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
    return client.request("GET", f"procedures/{args['procedure_id']}/participants")

async def invite_participants_handler(args: Dict) -> List[Dict]:
    return client.request("POST", f"procedures/{args['procedure_id']}/participants/bytinemail", json_data=args["invitations"])

async def block_participants_handler(args: Dict) -> Dict:
    data = {
        "participantIds": args["participant_ids"],
        "blockReason": args.get("block_reason")
    }
    client.request("PUT", f"procedures/{args['procedure_id']}/participants/block", json_data=data)
    return {"success": True}

async def unblock_participants_handler(args: Dict) -> Dict:
    client.request("PUT", f"procedures/{args['procedure_id']}/participants/unblock", json_data=args["participant_ids"])
    return {"success": True}

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

async def get_blocked_participants_handler(args: Dict) -> List[Dict]:
    return client.request("GET", f"procedures/{args['procedure_id']}/participants/blocked")

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
    """Upload files to storage"""
    import base64
    files = []
    for f in args["files"]:
        file_data = base64.b64decode(f["base64"])
        files.append(("files", (f["name"], file_data, "application/octet-stream")))
    
    headers = {
        'Authorization': f'Bearer {client.access_token}',
        'X-Bidzaar-Connector-User-Email': client.config.user_email
    }
    url = client._get_api_url("files/upload")
    response = client.session.post(url, files=files, headers=headers)
    response.raise_for_status()
    return response.json()


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


# MCP call handle

@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[types.TextContent]:
    try:
        result = await execute_tool(name, arguments)
        # ВСЕГДА сериализуем в JSON с двойными кавычками
        if isinstance(result, (dict, list)):
            text = json.dumps(result, ensure_ascii=False)
        else:
            text = str(result)
        return [types.TextContent(type="text", text=text)]
    except Exception as e:
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]


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

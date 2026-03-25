# bidzaar_mcp_server
Пример реализация MCP сервера для ии агентов для площадки ЭТП bidzaar 
https://phoenix.bidzaar.com/doc/connector/index.htm

###Tools list: 
create_procedure
get_procedure
update_procedure
delete_procedure_draft
publish_procedure
get_proposals_ids
get_proposals
get_proposals_ranks
rollback_proposal
get_participants
invite_participants
block_participants
unblock_participants
approve_participants
reject_participants
get_blocked_participants
get_events
complete_with_winners
complete_without_winners
finish_proposals_acceptance
get_stages
get_stages_full_info
announce_new_stage
get_choices
set_winners
get_companies_info
get_segments
get_tags
get_special_conditions
get_chat_spaces
get_chats
send_chat_message
upload_files
get_file
get_comparison_file
get_report_file
get_participant_applications
request_documents
apply_promo_code
add_additional_currency
update_additional_currencies
cancel_delayed_publication
cancel_stage
improve_description
return_to_evaluation
allow_price_change
reject_price_change_request

###Example:
from langchain_mcp_adapters.client import MultiServerMCPClient

MCP_SERVERS = {
    "bidzaar": {
       "command": "python",
       "args": ["/path/to/bidzaar_mcp_server.py"],
       "transport": "stdio",
   },
}
async def get_mcp_tools():
    tools = []
  
    if MCP_SERVERS:
        try:
            logger.info("Loading MCP servers...")
            stdio_client = MultiServerMCPClient(MCP_SERVERS)
            stdio_tools = await asyncio.wait_for(
                stdio_client.get_tools(),
                timeout=30.0
            )
            tools.extend(stdio_tools)
            logger.info(f" loaded {len(stdio_tools)} tools from stdio servers")
        except Exception as e:
            logger.error(f"Error loading stdio tools: {e}")
    
    logger.info(f"Total MCP tools loaded: {len(tools)}")
    return tools

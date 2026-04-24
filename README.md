# bidzaar_mcp_server
Пример реализация MCP сервера для ии агентов для площадки ЭТП bidzaar и навык (SKILL.MD) для публикации процедуры
https://phoenix.bidzaar.com/doc/connector/index.htm

### Tools list: 
- create_procedure
- get_procedure
- update_procedure
- delete_procedure_draft
- publish_procedure
- get_participants
- invite_participants
- block_participants
- unblock_participants
- get_blocked_participants
- get_events
- complete_without_winners
- get_companies_info
- get_tags
- upload_files
- get_file
- improve_description
- return_to_evaluation

### Example:

```python
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
...
```

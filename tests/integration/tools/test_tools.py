# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

import pytest


def is_tool_registered(llama_stack_client, toolgroup_id) -> bool:
    toolgroups = llama_stack_client.toolgroups.list()
    toolgroup_ids = [tg.identifier for tg in toolgroups]
    return toolgroup_id in toolgroup_ids


def test_toolsgroups_unregister(llama_stack_client):
    client = llama_stack_client

    providers = [
        p for p in client.providers.list()
        if p.api == "tool_runtime" and "search" in p.provider_id.lower()
    ]
    if not providers:
        pytest.skip("No search provider available for testing")

    toolgroup_id = "builtin::websearch"
    provider_id = providers[0].provider_id

    if not is_tool_registered(client, toolgroup_id):
        # Register the toolgroup first to ensure it exists
        client.toolgroups.register(
            toolgroup_id=toolgroup_id,
            provider_id=provider_id
        )

    # Verify it was registered
    assert is_tool_registered(client, toolgroup_id), f"Toolgroup {toolgroup_id} should be registered"

    # Unregister the tool
    client.toolgroups.unregister(
        toolgroup_id=toolgroup_id
    )

    # Verify it was indeed unregistered
    toolgroups_after = client.toolgroups.list()
    toolgroup_ids_after = [tg.identifier for tg in toolgroups_after]
    assert toolgroup_id not in toolgroup_ids_after, f"Toolgroup {toolgroup_id} should be unregistered"

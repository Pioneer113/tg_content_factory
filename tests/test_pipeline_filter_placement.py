"""Filter insertion must preserve messages loaded by CLI-built graphs."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_agent.cli.commands.pipeline import _upsert_filter_node
from tg_agent.cli.graph_builder import GraphBuilder
from tg_agent.cli.node_dsl import parse_node_spec
from tg_agent.models import PipelineEdge, PipelineGraph, PipelineNode, PipelineNodeType
from tg_agent.services.pipeline_nodes.base import NodeContext
from tg_agent.services.pipeline_nodes.handlers import (
    FetchMessagesHandler,
    FilterHandler,
    SearchQueryTriggerHandler,
    SourceHandler,
)
from tg_agent.services.pipeline_service import PipelineValidationError


class GraphService:
    def __init__(self, graph):
        self.graph = graph

    async def get_graph(self, pipeline_id):
        return self.graph

    async def add_node(self, pipeline_id, node):
        self.graph.nodes.append(node)
        return True

    async def replace_node(self, pipeline_id, node_id, replacement):
        self.graph.nodes = [replacement if n.id == node_id else n for n in self.graph.nodes]
        return True

    async def add_edge(self, pipeline_id, source, target):
        self.graph.edges.append(PipelineEdge(**{"from": source, "to": target}))
        return True

    async def remove_edge(self, pipeline_id, source, target):
        self.graph.edges = [e for e in self.graph.edges if (e.from_node, e.to_node) != (source, target)]
        return True


def cli_graph():
    builder = GraphBuilder()
    builder.add_node_spec(parse_node_spec("source"))
    builder.add_node_spec(parse_node_spec('llm_generate:prompt_template="{source_messages}"'))
    builder.set_sources([1001])
    return builder.build()


@pytest.mark.asyncio
async def test_cli_filter_runs_after_fetch_and_filters_loaded_messages():
    graph = cli_graph()
    svc = GraphService(graph)
    assert await _upsert_filter_node(svc, 1, {"type": "message_filter", "keywords": ["keep"]})
    edges = {(e.from_node, e.to_node) for e in graph.edges}
    assert edges == {
        ("source_0", "fetch_messages_0"),
        ("fetch_messages_0", "filter_1"),
        ("filter_1", "llm_generate_0"),
    }
    messages = [SimpleNamespace(text="keep this"), SimpleNamespace(text="discard this")]
    db = SimpleNamespace(repos=SimpleNamespace(messages=SimpleNamespace(get_recent_for_channels=AsyncMock(
        return_value=messages,
    ))))
    context = NodeContext()
    handlers = {
        PipelineNodeType.SOURCE: SourceHandler(),
        PipelineNodeType.FETCH_MESSAGES: FetchMessagesHandler(),
        PipelineNodeType.FILTER: FilterHandler(),
    }
    node_id = "source_0"
    while node_id != "llm_generate_0":
        node = next(n for n in graph.nodes if n.id == node_id)
        await handlers[node.type].execute(node.config, context, {"db": db})
        node_id = next(e.to_node for e in graph.edges if e.from_node == node_id)
    assert context.get_global("context_messages") == [messages[0]]


@pytest.mark.asyncio
@pytest.mark.parametrize("loader_type", [
    PipelineNodeType.FETCH_MESSAGES, PipelineNodeType.RETRIEVE_CONTEXT, PipelineNodeType.SEARCH_QUERY_TRIGGER,
])
async def test_filter_uses_loader_type_and_rewires_all_consumers(loader_type):
    graph = PipelineGraph(nodes=[
        PipelineNode(id="consumer", name="consumer", type=PipelineNodeType.LLM_GENERATE),
        PipelineNode(id="custom-loader", name="loader", type=loader_type),
        PipelineNode(id="filter_1", name="notify", type=PipelineNodeType.NOTIFY),
    ], edges=[
        PipelineEdge(**{"from": "custom-loader", "to": "consumer"}),
        PipelineEdge(**{"from": "custom-loader", "to": "filter_1"}),
    ])
    assert await _upsert_filter_node(GraphService(graph), 1, {"keywords": ["keep"]})
    assert {(e.from_node, e.to_node) for e in graph.edges} == {
        ("custom-loader", "filter_2"), ("filter_2", "consumer"), ("filter_2", "filter_1"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("loader_count", [0, 2])
async def test_ambiguous_or_missing_loader_does_not_mutate_graph(loader_count):
    graph = PipelineGraph(nodes=[
        PipelineNode(id=f"load-{i}", name="loader", type=PipelineNodeType.FETCH_MESSAGES)
        for i in range(loader_count)
    ])
    before = graph.model_dump()
    with pytest.raises(PipelineValidationError, match="exactly one"):
        await _upsert_filter_node(GraphService(graph), 1, {})
    assert graph.model_dump() == before


@pytest.mark.asyncio
async def test_existing_filter_updates_without_changing_edges():
    graph = cli_graph()
    svc = GraphService(graph)
    assert await _upsert_filter_node(svc, 1, {"keywords": ["first"]})
    edges = list(graph.edges)
    assert await _upsert_filter_node(svc, 1, {"keywords": ["second"]})
    assert graph.edges == edges
    filters = [n for n in graph.nodes if n.type == PipelineNodeType.FILTER]
    assert len(filters) == 1
    assert filters[0].config == {"keywords": ["second"]}


@pytest.mark.asyncio
async def test_search_trigger_filter_applies_to_search_messages():
    builder = GraphBuilder()
    builder.add_node_spec(parse_node_spec("search_query_trigger:query=news"))
    builder.add_node_spec(parse_node_spec("forward"))
    graph = builder.build()
    assert await _upsert_filter_node(GraphService(graph), 1, {"keywords": ["keep"]})
    assert {(e.from_node, e.to_node) for e in graph.edges} == {
        ("search_query_trigger_0", "filter_1"), ("filter_1", "forward_0"),
    }
    messages = [
        SimpleNamespace(text="keep this", channel_title="News"),
        SimpleNamespace(text="discard this", channel_title="News"),
    ]
    search = SimpleNamespace(search_local=AsyncMock(return_value=SimpleNamespace(messages=messages)))
    context = NodeContext()
    await SearchQueryTriggerHandler().execute({"query": "news"}, context, {"search_engine": search})
    node = next(n for n in graph.nodes if n.type == PipelineNodeType.FILTER)
    await FilterHandler().execute(node.config, context, {})
    assert context.get_global("context_messages") == [messages[0]]


@pytest.mark.asyncio
async def test_cli_fetch_then_search_trigger_rejects_filter_before_mutation():
    builder = GraphBuilder()
    builder.add_node_spec(parse_node_spec("source"))
    builder.add_node_spec(parse_node_spec("search_query_trigger:query=news"))
    builder.add_node_spec(parse_node_spec("llm_generate"))
    builder.set_sources([1001])
    graph = builder.build()
    assert sum(n.type == PipelineNodeType.FETCH_MESSAGES for n in graph.nodes) == 1
    before = graph.model_dump()
    with pytest.raises(PipelineValidationError, match="exactly one message-writing node"):
        await _upsert_filter_node(GraphService(graph), 1, {"keywords": ["keep"]})
    assert graph.model_dump() == before

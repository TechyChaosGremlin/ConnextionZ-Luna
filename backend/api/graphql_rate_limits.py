"""Cost protected GraphQL root mutations using GraphQL execution semantics."""

from collections import Counter
from types import MappingProxyType

from graphql import GraphQLError, GraphQLSchema, get_operation_ast, parse, validate
from graphql.execution.collect_fields import collect_fields
from graphql.execution.values import get_variable_values
from graphql.language import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    OperationType,
    VariableNode,
)
from strawberry.http import GraphQLRequestData

from app.rate_limits import ActionLimit

MAX_QUERY_DEPTH = 10
MAX_SELECTED_FIELDS = 1000

MUTATION_ACTIONS = MappingProxyType(
    {
        "register": "auth",
        "login": "auth",
        "createPost": "create_post",
        "createComment": "create_comment",
        "addComment": "create_comment",
        "sharePost": "share_post",
        "follow": "follow",
        "createCollaboration": "collaboration_request",
        "sendMessage": "send_message",
        "startLiveStream": "start_live_stream",
    }
)

MUTATION_LIMITS = MappingProxyType(
    {
        "auth": ActionLimit(5),
        "create_post": ActionLimit(5),
        "create_comment": ActionLimit(20),
        "share_post": ActionLimit(10),
        "follow": ActionLimit(20),
        "collaboration_request": ActionLimit(5),
        "send_message": ActionLimit(30),
        "start_live_stream": ActionLimit(2),
    }
)


def query_complexity_error(
    schema: GraphQLSchema,
    request_data: GraphQLRequestData | list[GraphQLRequestData],
) -> GraphQLError | None:
    """Return an error when the selected operation(s) exceed the complexity limits."""
    operations = request_data if isinstance(request_data, list) else [request_data]
    selected_fields = 0
    max_depth = 0

    for operation_data in operations:
        complexity = _operation_complexity(schema, operation_data)
        if complexity is None:
            continue
        operation_depth, operation_fields = complexity
        max_depth = max(max_depth, operation_depth)
        selected_fields += operation_fields

    if max_depth > MAX_QUERY_DEPTH:
        return GraphQLError(
            f"Query depth limit of {MAX_QUERY_DEPTH} exceeded.",
            extensions={
                "code": "QUERY_DEPTH_LIMIT_EXCEEDED",
                "statusCode": 400,
                "limit": MAX_QUERY_DEPTH,
            },
        )
    if selected_fields > MAX_SELECTED_FIELDS:
        return GraphQLError(
            f"Selected field limit of {MAX_SELECTED_FIELDS} exceeded.",
            extensions={
                "code": "QUERY_FIELD_LIMIT_EXCEEDED",
                "statusCode": 400,
                "limit": MAX_SELECTED_FIELDS,
            },
        )
    return None


def _operation_complexity(
    schema: GraphQLSchema,
    request_data: GraphQLRequestData,
) -> tuple[int, int] | None:
    """Measure one validated selected operation, expanding included fragments."""
    if not request_data.query:
        return None
    try:
        document = parse(request_data.query)
    except GraphQLError:
        return None
    if validate(schema, document):
        return None

    operation = get_operation_ast(document, request_data.operation_name)
    if operation is None or operation.operation == OperationType.SUBSCRIPTION:
        return None
    variables = get_variable_values(
        schema, operation.variable_definitions or (), request_data.variables or {}
    )
    if isinstance(variables, list):
        return None

    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }
    pending = [(operation.selection_set, 0, frozenset())]
    selected_fields = 0
    max_depth = 0

    while pending:
        selection_set, parent_depth, fragment_path = pending.pop()
        for selection in selection_set.selections:
            if not _should_include(selection, variables):
                continue
            if isinstance(selection, FieldNode):
                selected_fields += 1
                field_depth = parent_depth + 1
                max_depth = max(max_depth, field_depth)
                if selection.selection_set is not None:
                    pending.append((selection.selection_set, field_depth, fragment_path))
            elif isinstance(selection, InlineFragmentNode):
                pending.append((selection.selection_set, parent_depth, fragment_path))
            elif isinstance(selection, FragmentSpreadNode):
                name = selection.name.value
                fragment = fragments.get(name)
                if fragment is not None and name not in fragment_path:
                    pending.append(
                        (fragment.selection_set, parent_depth, fragment_path | {name})
                    )

            if selected_fields > MAX_SELECTED_FIELDS:
                return max_depth, selected_fields
            if max_depth > MAX_QUERY_DEPTH:
                return max_depth, selected_fields

    return max_depth, selected_fields


def _should_include(node, variables: dict) -> bool:
    for directive in node.directives:
        name = directive.name.value
        if name not in {"skip", "include"}:
            continue
        condition = next(
            (argument.value for argument in directive.arguments if argument.name.value == "if"),
            None,
        )
        if isinstance(condition, VariableNode):
            value = variables.get(condition.name.value)
        else:
            value = getattr(condition, "value", None)
        if name == "skip" and value is True:
            return False
        if name == "include" and value is False:
            return False
    return True


def mutation_costs(schema: GraphQLSchema, request_data: GraphQLRequestData) -> Counter[str]:
    """Count executable fields, not HTTP requests or user-supplied alias names."""
    if not request_data.query:
        return Counter()
    try:
        document = parse(request_data.query)
    except GraphQLError:
        # Leave invalid documents to Strawberry's normal validation/error handling.
        return Counter()
    if validate(schema, document):
        return Counter()
    operation = get_operation_ast(document, request_data.operation_name)
    if operation is None or operation.operation != OperationType.MUTATION:
        return Counter()
    variables = get_variable_values(
        schema, operation.variable_definitions or (), request_data.variables or {}
    )
    if isinstance(variables, list):
        return Counter()
    mutation_type = schema.mutation_type
    if mutation_type is None:
        return Counter()
    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }
    fields = collect_fields(schema, fragments, variables, mutation_type, operation.selection_set)
    return Counter(
        MUTATION_ACTIONS[nodes[0].name.value]
        for nodes in fields.values()
        if nodes[0].name.value in MUTATION_ACTIONS
    )

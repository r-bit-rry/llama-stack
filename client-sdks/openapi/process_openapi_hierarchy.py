#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

"""
Process OpenAPI spec to extract tag hierarchy and create dummy endpoints.

This script:
1. Parses an OpenAPI YAML file
2. Extracts tag hierarchies from endpoint tags (e.g., [x, y, z] -> x.y.z)
3. Reduces endpoint tags to only the leaf tag
4. Creates dummy endpoints for non-leaf tags
5. Outputs the hierarchy and modified spec
"""

import argparse
import sys
from pathlib import Path

try:
    import ruamel.yaml as yaml
except ImportError:
    print("Error: ruamel.yaml is required. Install with: pip install ruamel.yaml")
    sys.exit(1)


def build_hierarchy_from_tags(tags: list[str], hierarchy: dict) -> None:
    """Build nested hierarchy from tag list.

    Args:
        tags: List of tags in hierarchical order (e.g., ['x', 'y', 'z'])
        hierarchy: Dictionary to build the hierarchy in
    """
    current = hierarchy
    for tag in tags:
        if tag not in current:
            current[tag] = {}
        current = current[tag]


def get_leaf_tag(tags: list[str]) -> str | None:
    """Get the last (leaf) tag from a list.

    Args:
        tags: List of tags

    Returns:
        The last tag in the list, or None if empty
    """
    return tags[-1] if tags else None


def convert_oneof_const_to_enum(schema):
    """Convert oneOf with const values to enum.

    OpenAPI Generator doesn't handle oneOf with const values well - it generates
    multiple identical validators. This converts them to proper enum schemas.

    Args:
        schema: Schema dictionary to convert

    Returns:
        Converted schema with enum instead of oneOf, or original if not applicable
    """
    if not isinstance(schema, dict) or "oneOf" not in schema:
        return schema

    one_of = schema["oneOf"]
    if not isinstance(one_of, list):
        return schema

    # Check if all items have const
    if not all(isinstance(item, dict) and "const" in item for item in one_of):
        return schema

    # Extract const values and type
    enum_values = [item["const"] for item in one_of]
    schema_type = one_of[0].get("type", "string")

    # Create new enum schema
    new_schema = {"type": schema_type, "enum": enum_values}

    # Preserve other fields (description, title, etc.)
    for key in schema:
        if key not in ("oneOf", "type", "enum"):
            new_schema[key] = schema[key]

    return new_schema


def fix_oneof_const_schemas(obj):
    """Recursively fix oneOf-const patterns in the spec.

    Args:
        obj: Object to process (dict, list, or primitive)

    Returns:
        Processed object with oneOf-const patterns converted to enums
    """
    if isinstance(obj, dict):
        # Check if this is a oneOf-const pattern
        if "oneOf" in obj:
            obj = convert_oneof_const_to_enum(obj)
        # Recursively process nested dicts
        return {k: fix_oneof_const_schemas(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fix_oneof_const_schemas(item) for item in obj]
    else:
        return obj


def process_openapi(input_file: str, output_file: str, hierarchy_file: str) -> None:
    """Process OpenAPI spec to extract hierarchy and create dummy endpoints.

    Args:
        input_file: Path to input OpenAPI YAML file
        output_file: Path to output modified OpenAPI YAML file
        hierarchy_file: Path to output hierarchy YAML file
    """
    # Initialize YAML loader/dumper with ruamel.yaml
    yaml_handler = yaml.YAML()
    yaml_handler.preserve_quotes = True
    yaml_handler.default_flow_style = False

    # Load the OpenAPI spec
    print(f"Loading OpenAPI spec from: {input_file}")
    with open(input_file) as f:
        spec = yaml_handler.load(f)

    api_hierarchy = {}
    all_tags = set()
    tags_with_endpoints = set()

    # Iterate through all paths and operations
    print("\nProcessing endpoints...")
    endpoint_count = 0
    for path, path_item in spec.get("paths", {}).items():
        for method in ["get", "post", "put", "delete", "patch", "options", "head", "trace"]:
            if method in path_item:
                operation = path_item[method]
                endpoint_count += 1

                if "tags" in operation and operation["tags"]:
                    tags = operation["tags"]

                    # Build hierarchy
                    build_hierarchy_from_tags(tags, api_hierarchy)

                    # Add all tags to the set
                    all_tags.update(tags)

                    # Get leaf tag
                    leaf_tag = get_leaf_tag(tags)

                    # Mark leaf tag as having an endpoint
                    if leaf_tag:
                        tags_with_endpoints.add(leaf_tag)

                    # Update operation to only have leaf tag
                    operation["tags"] = [leaf_tag] if leaf_tag else []

                    print(f"  {method.upper():6} {path:50} tags: {tags} -> [{leaf_tag}]")

    # Find tags without endpoints
    tags_without_endpoints = all_tags - tags_with_endpoints

    # Create dummy endpoints for tags without endpoints
    if tags_without_endpoints:
        print(f"\nCreating dummy endpoints for {len(tags_without_endpoints)} non-leaf tags...")
        for tag in sorted(tags_without_endpoints):
            dummy_path = f"/dummy/{tag.lower().replace(' ', '-').replace('_', '-')}"
            spec["paths"][dummy_path] = {
                "get": {
                    "summary": f"Dummy endpoint for {tag} tag",
                    "description": f"This is a placeholder endpoint for the {tag} tag in the hierarchy",
                    "operationId": f"dummy_{tag.replace(' ', '_').replace('-', '_')}",
                    "tags": [tag],
                    "responses": {"200": {"description": "Success"}},
                    "x-operation-name": "dummy",
                }
            }
            print(f"  Created: GET {dummy_path} for tag [{tag}]")

    # Write api_hierarchy to file
    hierarchy_data = {
        "api_hierarchy": api_hierarchy,
        "all_tags": sorted(all_tags),
        "tags_with_endpoints": sorted(tags_with_endpoints),
        "tags_without_endpoints": sorted(tags_without_endpoints),
    }

    with open(hierarchy_file, "w") as f:
        yaml_handler.dump(hierarchy_data, f)

    # Fix oneOf-const patterns (convert to enums for proper code generation)
    print("\nFixing oneOf-const patterns...")
    spec = fix_oneof_const_schemas(spec)
    print("  ✓ OneOf-const patterns converted to enums")

    # Remove fields with default values from required lists
    print("\nRemoving fields with defaults from required lists...")
    if "components" in spec and "schemas" in spec["components"]:
        for schema_name, schema in spec["components"]["schemas"].items():
            if isinstance(schema, dict) and "required" in schema and "properties" in schema:
                fields_with_defaults = []
                for field_name, field_schema in schema["properties"].items():
                    if isinstance(field_schema, dict) and "default" in field_schema:
                        fields_with_defaults.append(field_name)

                if fields_with_defaults:
                    original_required = schema["required"].copy()
                    schema["required"] = [f for f in schema["required"] if f not in fields_with_defaults]
                    removed = [f for f in original_required if f not in schema["required"]]
                    if removed:
                        print(f"  ✓ {schema_name}: removed {removed} from required (have defaults)")
    print("  ✓ Fields with default values are now optional")

    # Fix Error model - make fields more flexible for better error handling
    if "components" in spec and "schemas" in spec["components"] and "Error" in spec["components"]["schemas"]:
        error_schema = spec["components"]["schemas"]["Error"]
        if "required" in error_schema:
            # Remove status and title from required fields
            error_schema["required"] = [f for f in error_schema["required"] if f not in ["status", "title"]]

        # Make detail field accept any type (string or object) since servers may return different formats
        if "properties" in error_schema and "detail" in error_schema["properties"]:
            # Change detail from strict string to flexible type
            error_schema["properties"]["detail"] = {
                "description": "Error detail - can be a string or structured error object",
                "oneOf": [{"type": "string"}, {"type": "object"}],
            }
            print("  ✓ Made Error model fields optional and flexible for better error handling")

    # Add x-unwrap-list-response extension for simple list responses
    print("\nAdding x-unwrap-list-response for simple list endpoints...")
    unwrapped_count = 0
    if "paths" in spec:
        for path, methods in spec["paths"].items():
            for method, operation in methods.items():
                if method.lower() not in ["get", "post", "put", "delete", "patch"]:
                    continue
                if not isinstance(operation, dict):
                    continue

                # Check if 200 response returns a List*Response schema
                if "responses" in operation and "200" in operation["responses"]:
                    response_200 = operation["responses"]["200"]
                    if "content" in response_200 and "application/json" in response_200["content"]:
                        schema_ref = response_200["content"]["application/json"].get("schema", {})

                        # Get the schema name from $ref
                        schema_name = None
                        if "$ref" in schema_ref:
                            schema_name = schema_ref["$ref"].split("/")[-1]

                        if schema_name and schema_name.startswith("List") and schema_name.endswith("Response"):
                            # Check if this is a simple list response (only has 'data' field with array)
                            # vs paginated response (has additional fields like has_more, url, etc.)
                            if "components" in spec and "schemas" in spec["components"]:
                                schema_def = spec["components"]["schemas"].get(schema_name, {})
                                if "properties" in schema_def:
                                    props = schema_def["properties"]
                                    # Simple list: only has 'data' field (and maybe 'object' for OpenAI compat)
                                    # Paginated: has has_more, url, first_id, last_id, etc.
                                    pagination_fields = {
                                        "has_more",
                                        "url",
                                        "first_id",
                                        "last_id",
                                        "next_page_token",
                                        "total",
                                    }
                                    has_pagination = any(field in props for field in pagination_fields)

                                    if not has_pagination and "data" in props:
                                        # This is a simple list response, mark it for unwrapping
                                        operation["x-unwrap-list-response"] = True
                                        unwrapped_count += 1
                                        op_id = operation.get("operationId", f"{method.upper()} {path}")
                                        print(f"  ✓ {op_id}: will unwrap {schema_name}")

    print(f"  ✓ Marked {unwrapped_count} endpoints for list unwrapping")

    # Write modified OpenAPI spec to output file
    with open(output_file, "w") as f:
        yaml_handler.dump(spec, f)

    # Print summary
    print(f"\n{'=' * 70}")
    print("Summary:")
    print(f"{'=' * 70}")
    print(f"  Total endpoints processed: {endpoint_count}")
    print(f"  Total tags found: {len(all_tags)}")
    print(f"  Tags with real endpoints: {len(tags_with_endpoints)}")
    print(f"  Tags without endpoints (dummy created): {len(tags_without_endpoints)}")
    print("\nOutput files:")
    print(f"  Modified OpenAPI spec: {output_file}")
    print(f"  API hierarchy: {hierarchy_file}")
    print("\nHierarchy structure:")
    print_hierarchy(api_hierarchy)


def print_hierarchy(hierarchy: dict, indent: int = 0) -> None:
    """Pretty print the hierarchy tree.

    Args:
        hierarchy: Hierarchy dictionary
        indent: Current indentation level
    """
    for key, value in hierarchy.items():
        print(f"  {'  ' * indent}{key}")
        if value:
            print_hierarchy(value, indent + 1)


def main():
    parser = argparse.ArgumentParser(
        description="Process OpenAPI spec to extract tag hierarchy and create dummy endpoints"
    )
    parser.add_argument(
        "--source",
        "-s",
        default="client-sdks/openapi/openapi.generator.yml",
        help="Source OpenAPI YAML file (default: openapi.generator.yml)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="client-sdks/openapi/openapi-processed.yml",
        help="Output OpenAPI YAML file (default: openapi-processed.yml)",
    )
    parser.add_argument(
        "--hierarchy", "-H", default="api-hierarchy.yml", help="API hierarchy output file (default: api-hierarchy.yml)"
    )

    args = parser.parse_args()

    # Check if source file exists
    if not Path(args.source).exists():
        print(f"Error: Source file '{args.source}' not found!")
        return 1

    try:
        process_openapi(args.source, args.output, args.hierarchy)
        return 0
    except Exception as e:
        print(f"Error processing OpenAPI spec: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())

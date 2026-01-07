#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

"""
Patch generated API classes to add hierarchical properties.

This script reads the api-hierarchy.yml file and patches the generated
API classes to add properties for child APIs, creating a nested API structure.

For example, if the hierarchy is {chat: {completions: {}}}, this will:
1. Add import in chat_api.py: from llama_stack_client.api.completions_api import CompletionsApi
2. Add property in chat_api.py: self.completions: CompletionsApi = None
"""

import argparse
import re
from pathlib import Path

import yaml


def to_snake_case(name: str) -> str:
    """Convert tag name to snake_case.

    Args:
        name: Tag name (e.g., "Chat", "DatasetIO")

    Returns:
        Snake case version (e.g., "chat", "dataset_io")
    """
    # Handle camelCase and PascalCase
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower().replace(" ", "_").replace("-", "_")


def to_pascal_case(name: str) -> str:
    """Convert tag name to PascalCase for class names.

    Args:
        name: Tag name (e.g., "chat", "dataset-io")

    Returns:
        PascalCase version (e.g., "Chat", "DatasetIo")
    """
    # Split by underscores, hyphens, or spaces
    words = re.split(r"[_\-\s]+", name)
    return "".join(word.capitalize() for word in words)


def extract_parent_child_pairs(hierarchy: dict, parent: str = None) -> list[tuple[str, str]]:
    """Extract all parent-child pairs from hierarchy.

    Args:
        hierarchy: Nested hierarchy dictionary
        parent: Current parent tag name

    Returns:
        List of (parent, child) tuples
    """
    pairs = []
    for key, value in hierarchy.items():
        if parent:
            pairs.append((parent, key))
        if value:
            pairs.extend(extract_parent_child_pairs(value, key))
    return pairs


def patch_api_file(api_file: Path, child_tag: str, package_name: str) -> bool:
    """Patch an API file to add a child API property.

    Args:
        api_file: Path to the parent API file
        child_tag: Tag name of the child API
        package_name: Package name for imports

    Returns:
        True if file was patched, False otherwise
    """
    if not api_file.exists():
        print(f"  ⚠ Warning: File {api_file} does not exist, skipping")
        return False

    # Read the file
    with open(api_file) as f:
        lines = f.readlines()

    # Convert child tag to appropriate naming
    child_snake = to_snake_case(child_tag)
    child_pascal = to_pascal_case(child_tag)
    child_class = f"{child_pascal}Api"
    child_module = f"{child_snake}_api"

    # Check if already patched
    import_line = f"from {package_name}.api.{child_module} import {child_class}\n"

    if any(import_line.strip() in line for line in lines):
        print(f"  [i] Already patched: {child_snake}")
        return False

    # Find class definition line
    class_line_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^class \w+Api:", line):
            class_line_idx = i
            break

    if class_line_idx is None:
        print(f"  ⚠ Warning: Could not find class definition in {api_file}")
        return False

    # Add import 2 lines before class definition
    import_idx = max(0, class_line_idx - 2)
    lines.insert(import_idx, import_line)

    # Find first occurrence of "self.api_client = api_client" after class definition
    api_client_line_idx = None
    for i in range(class_line_idx + 1, len(lines)):
        if "self.api_client = api_client" in lines[i]:
            api_client_line_idx = i
            break

    if api_client_line_idx is None:
        print(f"  ⚠ Warning: Could not find 'self.api_client = api_client' in {api_file}")
        return False

    # Get the indentation of the api_client line
    indent = len(lines[api_client_line_idx]) - len(lines[api_client_line_idx].lstrip())

    # Add property after api_client line
    property_line = f"{' ' * indent}self.{child_snake}: Optional[{child_class}] = None\n"
    lines.insert(api_client_line_idx + 1, property_line)

    # Write the patched file
    with open(api_file, "w") as f:
        f.writelines(lines)

    print(f"  ✓ Patched: {child_snake} -> {api_file.name}")
    return True


def patch_optional_import(api_file: Path) -> bool:
    """Ensure Optional is imported from typing.

    Args:
        api_file: Path to the API file

    Returns:
        True if import was added/updated, False otherwise
    """
    with open(api_file) as f:
        content = f.read()

    # Check if Optional is already imported
    if re.search(r"from typing import.*Optional", content):
        return False

    # Find existing typing import
    typing_import_match = re.search(r"from typing import ([^\n]+)", content)
    if typing_import_match:
        # Add Optional to existing import
        current_imports = typing_import_match.group(1)
        if "Optional" not in current_imports:
            new_imports = current_imports.rstrip() + ", Optional"
            content = content.replace(f"from typing import {current_imports}", f"from typing import {new_imports}")
            with open(api_file, "w") as f:
                f.write(content)
            return True
    else:
        # Add new typing import after other imports
        lines = content.split("\n")
        import_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                import_idx = i + 1
        lines.insert(import_idx, "from typing import Optional")
        with open(api_file, "w") as f:
            f.write("\n".join(lines))
        return True

    return False


def patch_llama_stack_client(client_file: Path, pairs: list[tuple[str, str]]) -> bool:
    """Patch LlamaStackClient to wire up parent-child relationships.

    Args:
        client_file: Path to the LlamaStackClient file
        pairs: List of (parent, child) tuples

    Returns:
        True if file was patched, False otherwise
    """
    if not client_file.exists():
        print(f"  ⚠ Warning: LlamaStackClient file {client_file} does not exist")
        return False

    # Read the file
    with open(client_file) as f:
        lines = f.readlines()

    # Find the comment "# Set up nested API structure based on x-nesting-config"
    comment_idx = None
    for i, line in enumerate(lines):
        if "# Set up nested API structure based on x-nesting-config" in line:
            comment_idx = i
            break

    if comment_idx is None:
        print(f"  ⚠ Warning: Could not find nesting config comment in {client_file}")
        return False

    # Check if already patched
    first_pair = pairs[0] if pairs else None
    if first_pair:
        parent_snake = to_snake_case(first_pair[0])
        child_snake = to_snake_case(first_pair[1])
        test_line = f"self.{parent_snake}.{child_snake} = self.{child_snake}"
        if any(test_line in line for line in lines):
            print("  [i] LlamaStackClient already patched")
            return False

    # Get indentation from the comment line itself (count whitespace before '#')
    comment_line = lines[comment_idx]
    indent = len(comment_line) - len(comment_line.lstrip())

    # Build the patch lines
    patch_lines = []
    patch_lines.append(f"{' ' * indent}# Wire up parent-child API relationships\n")

    for parent_tag, child_tag in pairs:
        parent_snake = to_snake_case(parent_tag)
        child_snake = to_snake_case(child_tag)
        patch_lines.append(f"{' ' * indent}self.{parent_snake}.{child_snake} = self.{child_snake}\n")

    # Insert after the comment
    insert_idx = comment_idx + 1
    for line in reversed(patch_lines):
        lines.insert(insert_idx, line)

    # Write the patched file
    with open(client_file, "w") as f:
        f.writelines(lines)

    print(f"  ✓ Patched LlamaStackClient with {len(pairs)} parent-child assignments")
    return True


def patch_apis(hierarchy_file: str, sdk_dir: str, package_name: str = "llama_stack_client") -> None:
    """Patch all API files based on hierarchy.

    Args:
        hierarchy_file: Path to api-hierarchy.yml
        sdk_dir: Path to generated SDK directory
        package_name: Python package name
    """
    # Load hierarchy
    print(f"Loading hierarchy from: {hierarchy_file}")
    with open(hierarchy_file) as f:
        data = yaml.safe_load(f)

    hierarchy = data.get("api_hierarchy", {})

    if not hierarchy:
        print("No hierarchy found in file")
        return

    # Extract parent-child pairs
    pairs = extract_parent_child_pairs(hierarchy)

    print(f"\nFound {len(pairs)} parent-child relationships")
    print("=" * 70)

    # SDK api directory
    api_dir = Path(sdk_dir) / package_name / "api"

    if not api_dir.exists():
        print(f"Error: API directory not found: {api_dir}")
        return

    patched_count = 0

    # Process each parent-child pair for individual API files
    print("\nPatching individual API files:")
    for parent_tag, child_tag in pairs:
        parent_snake = to_snake_case(parent_tag)
        parent_file = api_dir / f"{parent_snake}_api.py"

        print(f"\n{parent_tag} -> {child_tag}")

        # Ensure Optional is imported
        if parent_file.exists():
            patch_optional_import(parent_file)

        # Patch the parent file
        if patch_api_file(parent_file, child_tag, package_name):
            patched_count += 1

    # Patch LlamaStackClient
    print("\n" + "=" * 70)
    print("\nPatching LlamaStackClient:")
    client_file = Path(sdk_dir) / package_name / "llama_stack_client.py"
    if client_file.exists():
        patch_llama_stack_client(client_file, pairs)
    else:
        print(f"  ⚠ Warning: LlamaStackClient not found at {client_file}")

    print("\n" + "=" * 70)
    print(f"Summary: Patched {patched_count} API files")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Patch generated API classes with hierarchical properties")
    parser.add_argument(
        "--hierarchy", "-H", default="api-hierarchy.yml", help="API hierarchy file (default: api-hierarchy.yml)"
    )
    parser.add_argument("--sdk-dir", "-s", default="sdks/python", help="SDK directory (default: sdks/python)")
    parser.add_argument(
        "--package", "-p", default="llama_stack_client", help="Package name (default: llama_stack_client)"
    )

    args = parser.parse_args()

    # Check if hierarchy file exists
    if not Path(args.hierarchy).exists():
        print(f"Error: Hierarchy file '{args.hierarchy}' not found!")
        return 1

    # Check if SDK directory exists
    if not Path(args.sdk_dir).exists():
        print(f"Error: SDK directory '{args.sdk_dir}' not found!")
        return 1

    try:
        patch_apis(args.hierarchy, args.sdk_dir, args.package)
        return 0
    except Exception as e:
        print(f"Error patching API files: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())

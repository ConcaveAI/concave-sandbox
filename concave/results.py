"""
Result classes for sandbox operations.

This module defines dataclasses for command/code execution results and
custom list types for API responses.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecuteResult:
    """
    Result from executing a shell command in the sandbox.

    Attributes:
        stdout: Standard output from the command
        stderr: Standard error from the command
        returncode: Exit code from the command (0 = success)
        command: The original command that was executed
    """

    stdout: str
    stderr: str
    returncode: int
    command: str


@dataclass
class RunResult:
    """
    Result from running code in the sandbox.

    Attributes:
        stdout: Standard output from the code execution
        stderr: Standard error from the code execution
        returncode: Exit code from the code execution (0 = success)
        code: The original code that was executed
        language: The language that was executed (python or javascript)
    """

    stdout: str
    stderr: str
    returncode: int
    code: str
    language: str = "python"


class SandboxList(list):
    """
    Extended list class for sandbox listings with pagination metadata.
    
    This class extends the built-in list to include pagination information
    returned from the API, allowing easy iteration over sandboxes while
    providing access to pagination details.
    
    Attributes:
        has_more: Boolean indicating if more pages exist
        next_cursor: Cursor string for fetching the next page (None if no more pages)
        count: Number of sandboxes in this page
    
    Example:
        sandboxes = Sandbox.list(limit=10)
        for sbx in sandboxes:  # Works like a normal list
            print(sbx.id)
        
        if sandboxes.has_more:  # Access pagination metadata
            more = Sandbox.list(cursor=sandboxes.next_cursor)
    """
    
    def __init__(self, items, has_more: bool, next_cursor: Optional[str], count: int):
        """
        Initialize a SandboxList with items and pagination metadata.
        
        Args:
            items: List of Sandbox instances
            has_more: Whether more pages exist
            next_cursor: Cursor for next page (None if no more pages)
            count: Number of items in this page
        """
        super().__init__(items)
        self.has_more = has_more
        self.next_cursor = next_cursor
        self.count = count


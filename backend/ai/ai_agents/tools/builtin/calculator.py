"""Calculator tool."""

import ast
import operator

from ..base import Tool, ToolDefinition


class CalculatorTool(Tool):
    """Safe calculator for basic math expressions."""
    
    name = "calculator"
    description = "Evaluate mathematical expressions. Supports +, -, *, /, **, (), and common functions."
    
    # Safe operators
    _operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }
    
    async def execute(self, expression: str) -> str:
        """
        Evaluate a math expression.
        
        Args:
            expression: Math expression like "2 + 2" or "(10 * 5) / 2"
            
        Returns:
            Result as string
        """
        try:
            result = self._eval(expression)
            return str(result)
        except Exception as e:
            return f"Error: {str(e)}"
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematical expression to evaluate",
                    }
                },
                "required": ["expression"],
            },
        )
    
    def _eval(self, expression: str):
        """Safely evaluate expression."""
        tree = ast.parse(expression, mode='eval')
        return self._eval_node(tree.body)
    
    def _eval_node(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            op = self._operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand)
            op = self._operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(operand)
        else:
            raise ValueError(f"Unsupported expression type: {type(node).__name__}")

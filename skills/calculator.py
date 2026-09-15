"""Evaluate arithmetic safely (no eval): + - * / // % ** and parentheses."""
import ast
import operator
import re

SKILL = {
    "name": "calculator",
    "description": "Evaluate an arithmetic expression such as 12 * (3 + 4) or 2^10.",
    "triggers": [
        r"^\s*(?:calc(?:ulate)?|compute|evaluate|what(?:'s|\s+is))?\s*[-(]*\s*\d[\d.\s()]*(?:(?:\*\*|//|[-+*/%^x×÷])\s*[-(]*\s*\d[\d.\s()]*)+[?=]?\s*$",
    ],
    "version": 1,
    "origin": "builtin",
}

_OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _evaluate(node):
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_evaluate(node.operand))
    raise ValueError("unsupported expression")


def run(request, context):
    expression = re.sub(r"^\s*(?:calc(?:ulate)?|compute|evaluate|what(?:'s|\s+is))\s*", "", request, flags=re.IGNORECASE)
    expression = expression.strip().rstrip("?=").strip()
    python_expr = expression.replace("^", "**").replace("×", "*").replace("÷", "/")
    python_expr = re.sub(r"(?<=[\d)\s])x(?=[\s\d(])", "*", python_expr)
    try:
        value = _evaluate(ast.parse(python_expr, mode="eval"))
    except ZeroDivisionError:
        return "That divides by zero."
    except (SyntaxError, ValueError, OverflowError):
        return f"I couldn't evaluate '{expression}'."
    if isinstance(value, float):
        value = int(value) if value.is_integer() and abs(value) < 1e15 else round(value, 10)
    return f"{expression} = {value}"

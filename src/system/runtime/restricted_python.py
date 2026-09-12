"""Small, deterministic Python subset for code.python workflow nodes.

This is intentionally not a general Python sandbox. It evaluates a tiny AST subset
needed by data-shaping nodes without executing user code with ``exec``.
"""

import ast
from typing import Any


class RestrictedPythonError(ValueError):
    pass


def _evaluate(expression: ast.expr, values: dict[str, Any]) -> Any:
    if isinstance(expression, ast.Constant):
        return expression.value
    if isinstance(expression, ast.Name):
        if expression.id not in values:
            raise RestrictedPythonError(f"unknown name: {expression.id}")
        return values[expression.id]
    if isinstance(expression, ast.Dict):
        if any(key is None for key in expression.keys):
            raise RestrictedPythonError("dictionary unpacking is not supported")
        return {
            _evaluate(key, values): _evaluate(value, values)
            for key, value in zip(expression.keys, expression.values, strict=True)
        }
    if isinstance(expression, ast.List):
        return [_evaluate(item, values) for item in expression.elts]
    if isinstance(expression, ast.Tuple):
        return tuple(_evaluate(item, values) for item in expression.elts)
    if isinstance(expression, ast.Subscript):
        container = _evaluate(expression.value, values)
        key = _evaluate(expression.slice, values)
        try:
            return container[key]
        except (KeyError, IndexError, TypeError) as exc:
            raise RestrictedPythonError(f"invalid subscript: {key!r}") from exc
    if isinstance(expression, ast.UnaryOp) and isinstance(
        expression.op, (ast.UAdd, ast.USub)
    ):
        operand = _evaluate(expression.operand, values)
        if not isinstance(operand, (int, float)) or isinstance(operand, bool):
            raise RestrictedPythonError("unary +/- requires a number")
        return operand if isinstance(expression.op, ast.UAdd) else -operand
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
        try:
            return _evaluate(expression.left, values) + _evaluate(
                expression.right, values
            )
        except TypeError as exc:
            raise RestrictedPythonError("+ received incompatible values") from exc
    if isinstance(expression, ast.Call):
        if expression.keywords:
            raise RestrictedPythonError("keyword arguments are not supported")
        arguments = [_evaluate(argument, values) for argument in expression.args]
        if isinstance(expression.func, ast.Name) and expression.func.id == "len":
            if len(arguments) != 1:
                raise RestrictedPythonError("len() requires one argument")
            return len(arguments[0])
        if isinstance(expression.func, ast.Attribute):
            target = _evaluate(expression.func.value, values)
            method = expression.func.attr
            if method == "split" and isinstance(target, str):
                if len(arguments) > 1 or (
                    arguments and not isinstance(arguments[0], (str, type(None)))
                ):
                    raise RestrictedPythonError(
                        "str.split() received invalid arguments"
                    )
                return target.split(*arguments)
            if method == "join" and isinstance(target, str):
                if len(arguments) != 1:
                    raise RestrictedPythonError("str.join() requires one argument")
                try:
                    return target.join(arguments[0])
                except (TypeError, ValueError) as exc:
                    raise RestrictedPythonError(
                        "str.join() received invalid data"
                    ) from exc
        raise RestrictedPythonError(
            "only len(), str.split() and str.join() are allowed"
        )
    raise RestrictedPythonError(
        f"unsupported Python expression: {type(expression).__name__}"
    )


def execute_restricted_python(code: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Evaluate ``main(inputs)`` using the allow-listed expression subset."""
    try:
        module = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise RestrictedPythonError(f"invalid Python syntax: {exc.msg}") from exc

    if len(module.body) != 1 or not isinstance(module.body[0], ast.FunctionDef):
        raise RestrictedPythonError(
            "code must contain exactly one main(inputs) function"
        )
    function = module.body[0]
    if (
        function.name != "main"
        or len(function.args.args) != 1
        or function.args.args[0].arg != "inputs"
        or function.args.vararg is not None
        or function.args.kwarg is not None
        or function.args.defaults
        or function.args.kwonlyargs
        or function.decorator_list
    ):
        raise RestrictedPythonError("function signature must be main(inputs)")

    values: dict[str, Any] = {"inputs": inputs}
    for statement in function.body:
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(
                statement.targets[0], ast.Name
            ):
                raise RestrictedPythonError("assignments must target one local name")
            name = statement.targets[0].id
            if name == "inputs" or name.startswith("_"):
                raise RestrictedPythonError(f"assignment is not allowed: {name}")
            values[name] = _evaluate(statement.value, values)
            continue
        if isinstance(statement, ast.Return):
            if statement.value is None:
                raise RestrictedPythonError("main(inputs) must return an object")
            result = _evaluate(statement.value, values)
            if not isinstance(result, dict):
                raise RestrictedPythonError("main(inputs) must return a JSON object")
            return result
        raise RestrictedPythonError(
            f"unsupported Python statement: {type(statement).__name__}"
        )
    raise RestrictedPythonError("main(inputs) must return a JSON object")

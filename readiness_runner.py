from app.readiness import run_readiness
from app.readiness_db import execute_read
from app.readiness_policy import apply_bootstrap_policy


def format_result(result) -> str:
    lines = [f"PHASE_4_READINESS: {result.status.upper()}"]
    lines.extend(
        f"[{item.status.upper()}] {item.name} — {item.detail}"
        for item in result.checks
    )
    return "\n".join(lines)


def main() -> int:
    try:
        result = apply_bootstrap_policy(run_readiness(execute_read))
    except Exception as error:
        print("PHASE_4_READINESS: FAILED")
        print(f"[FAILED] dependency — {error}")
        return 1

    print(format_result(result))
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

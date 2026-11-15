"""The `dd-chat` command: starts the Streamlit app that ships inside this package."""
import sys
from pathlib import Path


def main() -> None:
    """Run `streamlit run app.py` with the settings that `.streamlit/config.toml` gives a checkout (that file is not installed)."""
    from streamlit.web import cli as stcli
    app = Path(__file__).resolve().parent / "app.py"
    # extra arguments (e.g. --server.port 8502) are passed on to Streamlit
    sys.argv = ["streamlit", "run", str(app), "--theme.baseFontSize=18", "--global.disableWidgetStateDuplicationWarning=true",
                "--browser.gatherUsageStats=false", *sys.argv[1:]]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()

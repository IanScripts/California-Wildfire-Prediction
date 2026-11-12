from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent

OUT = ROOT / "live" / "historical_expanded" / "nfdrs_runs" / "los_angeles"

WEATHER = (
    ROOT / "live" / "historical_expanded" / "fw21"
    / "los_angeles_2023_2025.fw21"
)

TEMPLATE = ROOT / "data" / "NFDRSInitSample.txt"
CLI = ROOT / "build" / "bin" / "NFDRS4_cli.exe"

def set_value(text, key, value):
    pattern = rf'(?m)^(\s*{re.escape(key)}\s*=\s*)"[^"]*"'
    updated, count = re.subn(
        pattern,
        lambda m: f'{m.group(1)}"{value}"',
        text
    )
    if count != 1:
        raise ValueError(f"Expected one setting for {key}, found {count}")
    return updated

def main():
    for path in [WEATHER, TEMPLATE, CLI]:
        if not path.is_file():
            raise FileNotFoundError(path)

    OUT.mkdir(parents=True, exist_ok=True)

    init_path = OUT / "historical_init.txt"
    cfg_path = OUT / "historical_run.txt"

    if init_path.exists() or cfg_path.exists():
        raise FileExistsError(
            "Historical configuration already exists; refusing to overwrite."
        )

    init = TEMPLATE.read_text(encoding="utf-8-sig")
    init = set_value(init, "latitude", "34.0522")
    init = set_value(init, "timeZoneOffset", "-8")

    init = (
        "# EXPERIMENTAL HISTORICAL NFDRS4 RUN\n"
        "# Fuel model and climate parameters are uncalibrated samples.\n"
        "# Fixed PST offset; daylight-saving behavior not yet validated.\n"
        + init
    )

    def posix(path):
        return path.resolve().as_posix()

    cfg = f'''# Experimental Los Angeles historical run
initFile = "{posix(init_path)}";
wxFile = "{posix(WEATHER)}";
loadFromStateFile = "";
saveToStateFile = "{posix(OUT / 'historical_state.nfdrs')}";
allOutputsFile = "{posix(OUT / 'historical_all.csv')}";
indexOutputFile = "{posix(OUT / 'historical_indexes.csv')}";
fuelMoisturesOutputFile = "{posix(OUT / 'historical_moistures.csv')}";
outputInterval = "0";
stationID = "historical_los_angeles";
useStoredOutputs = "0";
'''

    init_path.write_text(init, encoding="utf-8")
    cfg_path.write_text(cfg, encoding="utf-8")

    print("Historical configuration created successfully!")
    print("Weather:", WEATHER)
    print("Initialization:", init_path)
    print("Run configuration:", cfg_path)
    print("Executable:", CLI)

if __name__ == "__main__":
    main()

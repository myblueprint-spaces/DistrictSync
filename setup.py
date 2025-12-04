from setuptools import setup, find_packages

setup(
    name="sis_etl_tool",
    version="1.0.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "pandas>=1.4.0",
        "PyYAML>=6.0",
        "python-dateutil>=2.8.2",
    ],
    entry_points={
        "console_scripts": [
            "sis-etl=main:main",
        ],
    },
    python_requires=">=3.8",
)

#pyinstaller --onefile --name GDE2Acsv --add-data "config;config" --add-data "data/input;data/input" --add-data "data/output;data/output" --distpath dist --hidden-import=pandas --hidden-import=yaml --hidden-import=logging.config src/main.py
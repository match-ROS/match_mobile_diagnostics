from setuptools import find_packages, setup

setup(
    name="match_mobile_diagnostics",
    version="0.1.3",
    description="Read-only hardware diagnostics for MuR620 robots",
    license="MIT",
    packages=find_packages(),
    package_data={"match_mobile_diagnostics": ["profiles/*.json", "knowledge/*.json", "knowledge/*.md"]},
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/match_mobile_diagnostics"]),
        ("share/match_mobile_diagnostics", ["package.xml"]),
    ],
    python_requires=">=3.10",
    entry_points={"console_scripts": ["mur-diagnostics=match_mobile_diagnostics.cli:main"]},
)

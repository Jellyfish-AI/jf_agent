import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="jf_agent",
    version="0.0.18",
    description="An agent for collecting data for jellyfish",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Jellyfish-AI/jf_agent",
    packages=setuptools.find_packages(),
    install_requires=["jira", "tqdm", "stashy", "pyyaml>=4.2b1", "urllib3==1.24.2", "dateparser"],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    entry_points={"console_scripts": ["jf_agent=jf_agent.main:main"]},
)

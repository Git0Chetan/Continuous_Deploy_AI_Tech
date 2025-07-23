import google.generativeai as genai
import datetime
import os

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable not set")


genai.configure(api_key='{OPENAI_API_KEY}')
model = genai.GenerativeModel("gemini-1.5-flash")

source_code_file = "source_code.py" 

try:
    with open(source_code_file, "r", encoding="utf-8") as file:
        source_code = file.read()
except FileNotFoundError:
    print(f"Error: Source code file '{source_code_file}' not found.")
    exit(1)


prompt = (
    "You are a Python test automation engineer. "
    "Analyze the following Python code and generate comprehensive Pytest test functions for it. "
    "Create test cases that cover different scenarios including edge cases, valid inputs, invalid inputs, and error conditions. "
    "Use the pytest framework and include appropriate assertions. "
    "If the code contains functions, test each function separately. "
    "If the code contains classes, test the class methods and initialization. "
    "Only return the test code, do not explain.\n\n"
    f"Source code to test:\n{source_code}\n"
)

response = model.generate_content(prompt)
test_code = response.text.strip()

if test_code.startswith("```python"):
    test_code = test_code[9:]
if test_code.endswith("```"):
    test_code = test_code[:-3]


source_filename_without_ext = os.path.splitext(os.path.basename(source_code_file))[0]
filename = f"{source_filename_without_ext}_test.py"

output_directory = "gener_tests"
os.makedirs(output_directory, exist_ok=True)
output_path = os.path.join(output_directory, filename)

with open(output_path, "w", encoding="utf-8") as out_file:
    out_file.write("import pytest\n")
    out_file.write("import sys\n")
    out_file.write("import os\n")
    out_file.write(f"sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n")
    out_file.write(f"from {os.path.splitext(os.path.basename(source_code_file))[0]} import *\n\n")
    out_file.write(test_code + "\n")

print(f"Test code generated and saved to: {output_path}")
print(f"Generated tests for source file: {source_code_file}")
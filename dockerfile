# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory
WORKDIR /app

# Copy local code to the container
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

CMD ["python3", "app.py"]
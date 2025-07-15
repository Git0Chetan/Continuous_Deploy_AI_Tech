# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory
WORKDIR /app

# Copy local code to the container
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

# Run the Flask app using gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8080", "main:app"]
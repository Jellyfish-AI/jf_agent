FROM python:3.7 AS py-deps
COPY ./Pipfile ./Pipfile.lock ./
RUN pip install pipenv && \
    pipenv install --deploy --system --ignore-pipfile --clear

FROM python:3.7-alpine
COPY --from=py-deps /usr/local/lib/python3.7/site-packages /usr/local/lib/python3.7/site-packages
RUN addgroup -S jf_agent && adduser -S -G jf_agent jf_agent
COPY --chown=jf_agent:jf_agent . /home/jf_agent
RUN rm /home/jf_agent/Pipfile /home/jf_agent/Pipfile.lock
WORKDIR /home/jf_agent
ENV PYTHONPATH=/home/jf_agent \
    OUTPUT_BASEDIR=/home/jf_agent/output
USER jf_agent
CMD ["python", "jf_agent/main.py"]

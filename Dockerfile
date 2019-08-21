FROM python:3.7 AS py-deps
COPY ./Pipfile ./Pipfile.lock ./
RUN pip install pipenv && \
    pipenv install --deploy --system --ignore-pipfile --clear

FROM python:3.7-alpine
COPY --from=py-deps /usr/local/lib/python3.7/site-packages /usr/local/lib/python3.7/site-packages
RUN apk add groff && \
    pip install awscli && \
    addgroup -S jf_agent && \
    adduser -S -G jf_agent jf_agent && \
    rm -rf /var/cache/apk
COPY --chown=jf_agent:jf_agent . /home/jf_agent
RUN rm /home/jf_agent/Pipfile /home/jf_agent/Pipfile.lock
WORKDIR /home/jf_agent
ENV PYTHONPATH=/home/jf_agent
USER jf_agent
ENTRYPOINT ["python", "jf_agent/main.py"]

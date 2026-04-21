"""Citizen notifications subsystem: email and WhatsApp.

Events from the workflow engine are matched against configured triggers and
result in NotificationDelivery records that are dispatched by the arq worker.
"""

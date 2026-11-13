from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/extract/", views.api_extract),
    path("api/save/", views.api_save),
    path("api/records/", views.api_records),
    path("api/records/<int:pk>/delete/", views.api_delete),
    path("export/", views.export_xlsx, name="export"),
]

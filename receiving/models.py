from django.db import models


class ScanRecord(models.Model):
    """One scanned item, attached to a PO and the GRN it was received under."""
    po_number = models.CharField(max_length=60, db_index=True)
    grn_number = models.CharField(max_length=60, db_index=True)
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=300, blank=True)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.po_number} / {self.grn_number} / {self.title or self.url}"

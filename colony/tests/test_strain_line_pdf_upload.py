import re

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import StrainLine, StrainLineDocument
from users.models import UserProfile


class StrainLinePdfUploadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="pdfupload", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="pdfupload", password="x")
        self.line = StrainLine.objects.create(line_name="PdfStrain", name="PdfStrain")

    def _csrf_token_from(self, response) -> str:
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode())
        self.assertIsNotNone(match, "Expected csrfmiddlewaretoken in response HTML")
        return match.group(1)

    def test_edit_page_pdf_form_includes_csrf_token(self):
        response = self.client.get(reverse("colony:strain_line_edit", args=[self.line.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, reverse("colony:strain_line_upload_documents", args=[self.line.pk]))

    def test_upload_pdf_with_csrf_succeeds(self):
        edit_url = reverse("colony:strain_line_edit", args=[self.line.pk])
        page = self.client.get(edit_url)
        token = self._csrf_token_from(page)
        upload_url = reverse("colony:strain_line_upload_documents", args=[self.line.pk])
        pdf = SimpleUploadedFile("intro.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = self.client.post(
            upload_url,
            {
                "csrfmiddlewaretoken": token,
                "next": edit_url,
                "pdf_files": pdf,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StrainLineDocument.objects.filter(strain_line=self.line).count(), 1)

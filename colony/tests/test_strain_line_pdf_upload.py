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

    def _upload_form_token(self, response) -> str:
        html = response.content.decode()
        form_start = html.index('class="strain-pdf-upload-form"')
        form_chunk = html[form_start : form_start + 800]
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form_chunk)
        self.assertIsNotNone(match, "Expected csrf token inside PDF upload form")
        token = match.group(1)
        self.assertTrue(token.strip(), "PDF upload form CSRF token must not be empty")
        return token

    def test_edit_page_pdf_form_includes_csrf_token(self):
        response = self.client.get(reverse("colony:strain_line_edit", args=[self.line.pk]))
        self.assertEqual(response.status_code, 200)
        self._upload_form_token(response)

    def test_detail_page_pdf_form_includes_csrf_token(self):
        response = self.client.get(reverse("colony:strain_line_detail", args=[self.line.pk]))
        self.assertEqual(response.status_code, 200)
        self._upload_form_token(response)

    def test_upload_pdf_with_csrf_succeeds_from_detail_page(self):
        detail_url = reverse("colony:strain_line_detail", args=[self.line.pk])
        page = self.client.get(detail_url)
        token = self._upload_form_token(page)
        upload_url = reverse("colony:strain_line_upload_documents", args=[self.line.pk])
        pdf = SimpleUploadedFile("intro.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = self.client.post(
            upload_url,
            {
                "csrfmiddlewaretoken": token,
                "next": detail_url,
                "pdf_files": pdf,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StrainLineDocument.objects.filter(strain_line=self.line).count(), 1)

    def test_upload_pdf_with_csrf_succeeds_from_edit_page(self):
        edit_url = reverse("colony:strain_line_edit", args=[self.line.pk])
        page = self.client.get(edit_url)
        token = self._upload_form_token(page)
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

import re

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import StrainLine, StrainLineDocument
from core.models import Project
from users.models import UserProfile


class StrainLinePdfUploadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="pdfupload",
            email="pdfupload@example.test",
            password="x",
        )
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.ADMIN)
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="pdfupload", password="x")
        self.line = StrainLine.objects.create(line_name="PdfStrain", name="PdfStrain")

    def _upload_form_token(self, response) -> str:
        html = response.content.decode()
        form_start = html.index('class="strain-pdf-upload-form"')
        form_chunk = html[form_start : form_start + 1200]
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form_chunk)
        self.assertIsNotNone(match, "Expected csrf token inside PDF upload form")
        token = match.group(1)
        self.assertTrue(token.strip(), "PDF upload form CSRF token must not be empty")
        return token

    def test_edit_page_has_pdf_upload_form(self):
        response = self.client.get(reverse("colony:strain_line_edit", args=[self.line.pk]))
        self.assertEqual(response.status_code, 200)
        self._upload_form_token(response)
        self.assertContains(response, "Add another PDF")
        self.assertContains(response, "Save PDF(s)")
        self.assertContains(response, 'id="strain-pdf-upload-row-template"')

    def test_detail_page_has_pdf_upload_form(self):
        response = self.client.get(reverse("colony:strain_line_detail", args=[self.line.pk]))
        self.assertEqual(response.status_code, 200)
        self._upload_form_token(response)
        self.assertContains(response, "Save PDF(s)")
        self.assertContains(response, "Attach up to")

    def test_upload_pdf_from_edit_uses_description_as_name(self):
        edit_url = reverse("colony:strain_line_edit", args=[self.line.pk])
        page = self.client.get(edit_url)
        token = self._upload_form_token(page)
        upload_url = reverse("colony:strain_line_upload_documents", args=[self.line.pk])
        pdf = SimpleUploadedFile("random-upload-name.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = self.client.post(
            upload_url,
            {
                "csrfmiddlewaretoken": token,
                "next": edit_url,
                "pdf_file": pdf,
                "pdf_description_kind": "genotype_info",
            },
        )
        self.assertEqual(response.status_code, 302)
        doc = StrainLineDocument.objects.get(strain_line=self.line)
        self.assertEqual(doc.description, "Genotype info")
        self.assertEqual(doc.display_name, "Genotype info")
        self.assertTrue(doc.file.name.endswith(".pdf"))
        self.assertIn("Genotype", doc.file.name)

    def test_upload_pdf_from_detail(self):
        detail_url = reverse("colony:strain_line_detail", args=[self.line.pk])
        page = self.client.get(detail_url)
        token = self._upload_form_token(page)
        upload_url = reverse("colony:strain_line_upload_documents", args=[self.line.pk])
        pdf = SimpleUploadedFile("detail.pdf", b"%PDF-1.4 detail", content_type="application/pdf")
        response = self.client.post(
            upload_url,
            {
                "csrfmiddlewaretoken": token,
                "next": detail_url,
                "pdf_file": pdf,
                "pdf_description_kind": "strain_line_info",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(StrainLineDocument.objects.filter(strain_line=self.line).exists())

    def test_upload_multiple_pdfs_from_edit(self):
        edit_url = reverse("colony:strain_line_edit", args=[self.line.pk])
        page = self.client.get(edit_url)
        token = self._upload_form_token(page)
        upload_url = reverse("colony:strain_line_upload_documents", args=[self.line.pk])
        response = self.client.post(
            upload_url,
            {
                "csrfmiddlewaretoken": token,
                "next": edit_url,
                "pdf_files": [
                    SimpleUploadedFile("husbandry.pdf", b"%PDF-1.4 husbandry", content_type="application/pdf"),
                    SimpleUploadedFile("custom.pdf", b"%PDF-1.4 custom", content_type="application/pdf"),
                ],
                "pdf_description_kinds": ["husbandry", "custom"],
                "pdf_description_customs": ["", "Colony notes batch A"],
            },
        )
        self.assertEqual(response.status_code, 302)
        docs = list(StrainLineDocument.objects.filter(strain_line=self.line).order_by("description"))
        self.assertEqual(len(docs), 2)
        self.assertEqual([doc.description for doc in docs], ["Colony notes batch A", "Husbandry"])

    def test_manager_can_upload_but_not_delete_pdf(self):
        manager = get_user_model().objects.create_user(username="pdfmanager", password="x")
        UserProfile.objects.filter(user=manager).update(role=UserProfile.Role.MANAGER)
        client = Client(enforce_csrf_checks=True)
        client.login(username="pdfmanager", password="x")
        edit_url = reverse("colony:strain_line_edit", args=[self.line.pk])
        page = client.get(edit_url)
        self.assertContains(page, 'class="strain-pdf-upload-form"')
        self.assertNotContains(page, 'class="strain-pdf-delete-form"')
        token = self._upload_form_token(page)
        upload_url = reverse("colony:strain_line_upload_documents", args=[self.line.pk])
        response = client.post(
            upload_url,
            {
                "csrfmiddlewaretoken": token,
                "next": edit_url,
                "pdf_file": SimpleUploadedFile("manager.pdf", b"%PDF-1.4 manager", content_type="application/pdf"),
                "pdf_description_kind": "husbandry",
            },
        )
        self.assertEqual(response.status_code, 302)
        doc = StrainLineDocument.objects.get(strain_line=self.line)
        delete_url = reverse("colony:strain_line_document_delete", args=[self.line.pk, doc.pk])
        delete_response = client.post(
            delete_url,
            {
                "csrfmiddlewaretoken": token,
                "next": edit_url,
            },
        )
        self.assertIn(delete_response.status_code, {302, 403})
        self.assertTrue(StrainLineDocument.objects.filter(pk=doc.pk).exists())


class StrainLineRelatedRecordLinkTests(TestCase):
    def setUp(self):
        self.viewer = get_user_model().objects.create_user(username="viewer", password="x")
        self.owner = get_user_model().objects.create_user(username="otherowner", password="x")
        self.client.login(username="viewer", password="x")
        self.strain = StrainLine.objects.create(line_name="LinkStrain", name="LinkStrain")
        self.project = Project.objects.create(name="Other Project", owner=self.owner)
        from colony.models import Mouse

        Mouse.objects.create(
            mouse_uid="M-LINK-1",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
        )

    def test_strain_line_mouse_link_shows_mice_for_all_owners(self):
        url = reverse("mice:mouse_list")
        response = self.client.get(url, {"strain_line_id": self.strain.pk})
        self.assertContains(response, "M-LINK-1")

    def test_detail_page_does_not_list_mice_table(self):
        response = self.client.get(reverse("colony:strain_line_detail", args=[self.strain.pk]))
        self.assertNotContains(response, "Mice on this strain")
        self.assertNotContains(response, "Cages for this strain")
